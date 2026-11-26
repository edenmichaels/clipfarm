"""Projected earnings per streamer x campaign x platform (AUD, low / likely / high).

A port of market_forecast.py (project docs) from "market segments" to individual campaigns:

  hype(t)     = base + spike * exp(-t / tau) + event bumps          launch hype fading over time
  supply(t)   = 1 / (1 + exp(-(t - t_half) / k))                   clipper flood after launch
  median(t)   = platform_median * account * popularity * (1 + (hype(t)-1) * seg_hype)
                / (1 + crowd * supply(t))                          crowd grows with clippers on the campaign
  views/clip  ~ LogNormal(log median(t), sigma) * (1 - view_loss)
  pay-per-view: pay = clamp(views * rate / 1000, 0, max_payout) if pay >= min_payout else 0
                net = pay * approved * (1 - fee); cumulative earnings capped by my share of the budget left
  prize pool:   share = pool * my_views / total_views if my_views >= threshold else 0, paid when it ends

Ranking uses a risk-adjusted value, 0.5 * mean + 0.5 * median, so a campaign that pays per approved
clip (median > 0) beats a prize pool with the same mean (median usually 0) - unless the pool really
projects higher. Everything is labelled ESTIMATE in the UI; fields still "needs confirming" add a warning.

Once there are 2+ weeks of the user's own post metrics, `refit()` replaces the platform medians,
spread and approval rate with fitted values.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from .. import db
from .fields import effective

# ---- model parameters (from market_forecast.py; editable, refitted from real results) ---------------------
HYPE = dict(base=1.3, spike=3.5, tau=9.0)
EVENTS = [(2, 0.6, 2), (36, 0.5, 4), (60, 0.8, 5)]     # (day after launch, extra hype, width)
SUPPLY = dict(t_half=8.0, k=3.0)
PLATFORMS = {      # median views per clip for a new account, lognormal spread
    "tiktok": dict(label="TikTok", median=1800, sigma=1.7),
    "youtube_shorts": dict(label="YouTube Shorts", median=1400, sigma=1.6),
    "instagram_reels": dict(label="Instagram", median=1000, sigma=1.6),
    "x": dict(label="X", median=400, sigma=1.5),
}
ACCOUNT_PLATFORM = {"tiktok": "tiktok", "youtube_shorts": "youtube", "instagram_reels": "instagram", "x": "x"}
VIEW_LOSS = 0.2
SEG_HYPE = 1.2
POOL_EFFECTIVE_CPM = 0.5     # a pool usually ends up paying ~$0.50 per 1K of all qualifying views
POOL_DAYS_DEFAULT = 6
N_SIMS = 400


def hype(t: float) -> float:
    h = HYPE["base"] + HYPE["spike"] * math.exp(-max(t, 0) / HYPE["tau"])
    for day, amp, w in EVENTS:
        h += amp * math.exp(-((t - day) ** 2) / (2 * w * w))
    return h


def supply(t: float) -> float:
    return 1 / (1 + math.exp(-(t - SUPPLY["t_half"]) / SUPPLY["k"]))


@dataclass
class Inputs:
    platform: str
    campaign_type: str
    rate: float                 # USD per 1K views (pay-per-view)
    min_payout: float           # per clip, USD
    min_views: int              # per clip
    max_payout: float           # per clip, USD
    fee: float
    approval: float
    budget_remaining: float | None
    pool_size: float = 0.0
    view_threshold: int = 0
    days_left: float | None = None
    clippers: int = 0
    avg_viewers: float = 0.0
    followers: int = 0
    day: float = 0.0            # days since launch
    clips_per_day: float = 4.0
    usd_to_aud: float = 1.53
    median: float | None = None
    sigma: float | None = None
    warnings: list[str] = field(default_factory=list)


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _median_views(x: Inputs, t: float) -> float:
    p = PLATFORMS[x.platform]
    base = x.median or p["median"]
    # New accounts get far fewer views: 0 followers -> 0.35x, 1K -> ~0.6x, 10K -> ~0.85x, 100K -> ~1.1x.
    account = min(2.0, 0.35 + 0.25 * math.log10(1 + max(x.followers, 0) / 100))
    popularity = min(1.5, max(0.6, 0.6 + 0.15 * math.log10(max(x.avg_viewers, 0) + 1)))
    crowd = 1.0 + x.clippers / 100
    return base * account * popularity * (1 + (hype(t) - 1) * SEG_HYPE) / (1 + crowd * supply(t))


def simulate(x: Inputs, horizon: int = 30, n_sims: int = N_SIMS, seed: int = 0) -> dict:
    """Returns AUD arrays: per_clip [n], per_day [n], d7 [n], d30 [n]."""
    rng = np.random.default_rng(seed)
    sigma = x.sigma or PLATFORMS[x.platform]["sigma"]
    cpd = max(0.0, x.clips_per_day)
    k = max(1, int(round(cpd)))
    days = horizon if x.days_left is None else max(0, min(horizon, int(math.ceil(x.days_left))))
    daily = np.zeros((n_sims, horizon))
    per_clip_first = np.zeros(n_sims)
    if x.campaign_type == "prize_pool":
        pool_days = days if x.days_left is not None else POOL_DAYS_DEFAULT
        total_views = max(1.0, x.pool_size * 1000 / POOL_EFFECTIVE_CPM)
        mine = np.zeros(n_sims)
        for d in range(pool_days):
            med = _median_views(x, x.day + d)
            v = rng.lognormal(math.log(med), sigma, size=(n_sims, k)) * (1 - VIEW_LOSS)
            v = np.where(v >= x.min_views, v, 0.0) * (rng.random((n_sims, k)) < x.approval)
            mine += v.sum(axis=1) * (cpd / k)
        share = np.where(mine >= x.view_threshold, np.minimum(1.0, mine / total_views), 0.0)
        payout = x.pool_size * share * (1 - x.fee) * x.usd_to_aud
        if pool_days > 0:
            daily[:, min(pool_days, horizon) - 1] = payout
        n_clips = max(1.0, cpd * max(pool_days, 1))
        per_clip_first = payout / n_clips
    else:
        cap_total = None
        if x.budget_remaining is not None:
            # You won't get all of what's left: other clippers draw on the same budget.
            cap_total = x.budget_remaining / (1 + x.clippers / 5) * x.usd_to_aud * (1 - x.fee)
        cum = np.zeros(n_sims)
        for d in range(days):
            med = _median_views(x, x.day + d)
            v = rng.lognormal(math.log(med), sigma, size=(n_sims, k)) * (1 - VIEW_LOSS)
            gross = v * x.rate / 1000
            ok = (gross >= x.min_payout) & (v >= x.min_views)
            pay = np.minimum(np.where(ok, gross, 0.0), x.max_payout) * (rng.random((n_sims, k)) < x.approval)
            net = pay * (1 - x.fee) * x.usd_to_aud
            if d == 0:
                per_clip_first = net.mean(axis=1)
            day_total = net.sum(axis=1) * (cpd / k)
            if cap_total is not None:
                day_total = np.minimum(day_total, np.maximum(0, cap_total - cum))
            cum += day_total
            daily[:, d] = day_total
    return {"per_clip": per_clip_first, "per_day": daily[:, 0] if days else np.zeros(n_sims),
            "d7": daily[:, :7].sum(axis=1), "d30": daily[:, :30].sum(axis=1)}


def band(a: np.ndarray) -> tuple[float, float, float]:
    """(low, likely, high) = (P10, mean, P90)."""
    return (round(float(np.percentile(a, 10)), 2), round(float(a.mean()), 2), round(float(np.percentile(a, 90)), 2))


def rank_value(a: np.ndarray) -> float:
    return float(0.5 * a.mean() + 0.5 * np.median(a))


# ---- glue to the database -------------------------------------------------------------------------
def _launch_day() -> float:
    launch = datetime.fromisoformat(db.get_setting("game_launch_date", "2026-11-19")).replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - launch).total_seconds() / 86400)


def _days_left(prog: dict) -> float | None:
    if not prog.get("ends_at"):
        return None
    try:
        end = datetime.fromisoformat(prog["ends_at"]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 86400)


def pc_minutes_per_clip() -> float:
    """Measured from finished render jobs when there are enough, else the setting (default 4 min)."""
    rows = db.q("""SELECT (julianday(updated_at) - julianday(created_at)) * 1440 m FROM jobs
                   WHERE kind='render_moment' AND status='done' ORDER BY id DESC LIMIT 30""")
    ms = [r["m"] for r in rows if r["m"] and 0.2 < r["m"] < 60]
    if len(ms) >= 5:
        return float(np.median(ms)) + 1.0       # + fetch/detect overhead
    return float(db.get_setting("pc_minutes_per_clip", 4.0))


def inputs_for(streamer: dict, prog: dict, platform: str, clips_per_day: float) -> Inputs:
    warnings = []
    flags = json.loads(prog.get("needs_confirming") or "[]")
    for f in flags:
        warnings.append(f"'{f.replace('_', ' ')}' needs confirming (using {effective(prog, f)!r})")
    fitted = db.get_setting("fitted_model") or {}
    pf = fitted.get("platforms", {}).get(platform, {})
    acct = db.q1("SELECT followers FROM accounts WHERE platform=? ORDER BY followers DESC", (ACCOUNT_PLATFORM[platform],))
    avg = db.q1("""SELECT AVG(viewers) v FROM streamer_snapshots WHERE streamer_id=?
                   AND taken_at >= datetime('now','-7 days')""", (streamer["id"],))["v"] or 0
    rate = float(effective(prog, "payout_per_1k") or 0)
    if prog.get("campaign_type") != "prize_pool" and not rate:
        warnings.append("No payout rate set: projected as $0")
    return Inputs(
        platform=platform, campaign_type=prog.get("campaign_type") or "pay_per_view", rate=rate,
        min_payout=float(effective(prog, "min_payout") or 0), min_views=int(effective(prog, "min_views") or 0),
        max_payout=float(effective(prog, "max_payout") or 1e9), fee=float(effective(prog, "fee")),
        approval=float(fitted.get("approval") or effective(prog, "approval_rate")),
        budget_remaining=prog.get("budget_remaining"), pool_size=float(prog.get("pool_size") or 0),
        view_threshold=int(prog.get("view_threshold") or 0), days_left=_days_left(prog),
        clippers=int(prog.get("clippers_joined") or 0), avg_viewers=float(avg),
        followers=int((acct or {}).get("followers") or 0), day=_launch_day(), clips_per_day=clips_per_day,
        usd_to_aud=float(db.get_setting("usd_to_aud", 1.53)), median=pf.get("median"), sigma=pf.get("sigma"),
        warnings=warnings)


def project(streamer: dict, prog: dict, platform: str, clips_per_day: float | None = None) -> dict:
    cpd = clips_per_day if clips_per_day is not None else float(db.get_setting("autopilot", {}).get(
        "clips_per_streamer_per_day", 4))
    x = inputs_for(streamer, prog, platform, cpd)
    sim = simulate(x, seed=_seed(streamer["id"], prog["id"], platform))
    per_clip = band(sim["per_clip"])
    rv = rank_value(sim["per_clip"])
    mins = pc_minutes_per_clip()
    out = {
        "streamer_id": streamer["id"], "program_id": prog["id"], "platform": platform,
        "per_clip": per_clip, "per_day": band(sim["per_day"]), "d7": band(sim["d7"]), "d30": band(sim["d30"]),
        "rank_value": round(rv, 3), "aud_per_pc_hour": round(rv * 60 / mins, 2), "clips_per_day": cpd,
        "warnings": x.warnings,
        "details": {"campaign_type": x.campaign_type, "rate_usd_per_1k": x.rate, "min_payout": x.min_payout,
                    "min_views": x.min_views, "effective_min_views": int(max(x.min_views, x.min_payout / x.rate * 1000))
                    if x.rate else x.min_views, "max_payout": x.max_payout, "fee": x.fee, "approval": x.approval,
                    "budget_remaining": x.budget_remaining, "clippers": x.clippers, "avg_viewers": round(x.avg_viewers),
                    "median_views_today": round(_median_views(x, x.day)), "day_since_launch": round(x.day, 1),
                    "hype": round(hype(x.day), 2), "usd_to_aud": x.usd_to_aud, "pc_minutes_per_clip": round(mins, 1),
                    "pool_size": x.pool_size, "view_threshold": x.view_threshold, "days_left": x.days_left,
                    "model": "fitted" if x.median else "default"},
    }
    return out


def save(p: dict) -> None:
    lo, li, hi = p["per_clip"]
    dlo, dli, dhi = p["per_day"]
    a, b, c = p["d7"]
    e, f, g = p["d30"]
    db.ex("""INSERT INTO projections(streamer_id, program_id, platform, per_clip_low, per_clip_likely, per_clip_high,
             per_day_low, per_day_likely, per_day_high, d7_low, d7_likely, d7_high, d30_low, d30_likely, d30_high,
             aud_per_pc_hour, clips_per_day, warnings, details, computed_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
             ON CONFLICT(streamer_id, program_id, platform) DO UPDATE SET per_clip_low=excluded.per_clip_low,
             per_clip_likely=excluded.per_clip_likely, per_clip_high=excluded.per_clip_high,
             per_day_low=excluded.per_day_low, per_day_likely=excluded.per_day_likely, per_day_high=excluded.per_day_high,
             d7_low=excluded.d7_low, d7_likely=excluded.d7_likely, d7_high=excluded.d7_high,
             d30_low=excluded.d30_low, d30_likely=excluded.d30_likely, d30_high=excluded.d30_high,
             aud_per_pc_hour=excluded.aud_per_pc_hour, clips_per_day=excluded.clips_per_day,
             warnings=excluded.warnings, details=excluded.details, computed_at=excluded.computed_at""",
          (p["streamer_id"], p["program_id"], p["platform"], lo, li, hi, dlo, dli, dhi, a, b, c, e, f, g,
           p["aud_per_pc_hour"], p["clips_per_day"], json.dumps(p["warnings"]),
           json.dumps({**p["details"], "rank_value": p["rank_value"]})))


def allowed_platforms(prog: dict) -> list[str]:
    plats = effective(prog, "allowed_platforms") or []
    return [p for p in plats if p in PLATFORMS]


def project_all() -> list[dict]:
    """Projections for every linked streamer x active joined campaign x allowed platform."""
    from .matching import active_campaigns_for
    out = []
    streamers = db.q("""SELECT DISTINCT s.* FROM streamers s JOIN streamer_programs sp ON sp.streamer_id=s.id
                        AND sp.confirmed=1""")
    for s in streamers:
        for prog in active_campaigns_for(s["id"]):
            for plat in allowed_platforms(prog):
                p = project(s, prog, plat)
                save(p)
                out.append(p)
    out.sort(key=lambda p: p["aud_per_pc_hour"], reverse=True)
    return out


def best_aud_per_hour(streamer_id: int) -> float | None:
    r = db.q1("SELECT MAX(aud_per_pc_hour) v FROM projections WHERE streamer_id=?", (streamer_id,))
    return r["v"] if r and r["v"] is not None else None


# ---- refit from the user's own results -----------------------------------------------------------
def refit(min_days: int = 14, min_posts: int = 8) -> dict:
    """Fit per-platform median/spread from real views (latest metric of posts >= 3 days old) and the
    approval rate from submissions vs approved payouts. Needs 2+ weeks of history."""
    first = db.q1("SELECT MIN(posted_at) t FROM posts WHERE status='posted'")["t"]
    if not first or (datetime.now() - datetime.fromisoformat(first)).days < min_days:
        return {"fitted": False, "reason": f"Needs {min_days}+ days of posting history."}
    rows = db.q("""SELECT a.platform, (SELECT views FROM post_metrics pm WHERE pm.post_id=p.id ORDER BY taken_at DESC
                   LIMIT 1) views FROM posts p JOIN accounts a ON a.id=p.account_id
                   WHERE p.status='posted' AND p.posted_at <= datetime('now','-3 days')""")
    back = {v: k for k, v in ACCOUNT_PLATFORM.items()}
    plats = {}
    for plat in set(r["platform"] for r in rows):
        views = np.array([r["views"] for r in rows if r["platform"] == plat and r["views"]], dtype=float)
        if len(views) >= min_posts:
            logs = np.log(np.maximum(views, 1))
            plats[back.get(plat, plat)] = {"median": float(np.exp(np.median(logs))),
                                           "sigma": float(max(0.5, logs.std())), "n": int(len(views))}
    subs = db.q1("SELECT COUNT(*) n FROM submissions WHERE status='done'")["n"]
    appr = db.q1("SELECT COUNT(*) n FROM payouts WHERE status IN ('approved','paid')")["n"]
    model = {"platforms": plats, "fitted_at": datetime.now().isoformat(timespec="seconds")}
    if subs >= min_posts:
        model["approval"] = round(min(1.0, appr / subs), 3)
    db.set_setting("fitted_model", model)
    return {"fitted": True, **model}


def estimated_vs_actual() -> list[dict]:
    """Per campaign x platform: what we projected when posting vs what was paid (AUD)."""
    return db.q("""SELECT pr.id program_id, pr.name program, a.platform, COUNT(p.id) posts,
                   ROUND(SUM(COALESCE(p.projected_aud,0)),2) estimated_aud,
                   ROUND(SUM(CASE WHEN py.status IN ('approved','paid') THEN COALESCE(py.amount_aud, py.amount *
                        (SELECT CAST(value AS REAL) FROM settings WHERE key='usd_to_aud')) ELSE 0 END),2) actual_aud,
                   ROUND(SUM(CASE WHEN py.status='pending' THEN COALESCE(py.amount_aud,0) ELSE 0 END),2) pending_aud
                   FROM posts p JOIN accounts a ON a.id=p.account_id JOIN programs pr ON pr.id=p.program_id
                   LEFT JOIN payouts py ON py.post_id=p.id
                   WHERE p.status='posted' GROUP BY pr.id, a.platform ORDER BY pr.name""")
