"""The streamer list in three sections, each sorted by projected earnings (AUD per clip, likely value):

  joined         campaigns the user is in. Ready to clip.
  worth_joining  campaigns the user hasn't joined that project above their minimum $ per clip.
                 Ending, out-of-budget and not-paying-Australians campaigns drop out (with the reason).
  no_campaign    streamers nobody is paying clippers for. Clipping them still needs confirming.

No network calls: works from the database (rankings, links, projections) plus name matching.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .. import db
from . import forecast
from .autopilot import settings as autopilot_settings
from .matching import AUTO_LINK, _decisions, _score

SUGGEST = 0.6


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def campaign_state(p: dict) -> tuple[str, str | None]:
    """(state, reason). state: open | ending | out_of_budget | ended | no_au."""
    now = _now()
    status = p.get("status") or "active"
    end = None
    if p.get("ends_at"):
        try:
            end = datetime.fromisoformat(p["ends_at"])
        except ValueError:
            end = None
    if status == "ended" or (end and end <= now):
        return "ended", "Campaign has ended"
    if p.get("pays_au") == 0:
        return "no_au", "Doesn't pay Australian clippers"
    rem = p.get("budget_remaining")
    if status == "out_of_budget" or (p.get("campaign_type") != "prize_pool" and rem is not None
                                    and rem <= max(1.0, p.get("min_payout") or 0)):
        return "out_of_budget", "Budget is used up"
    if status == "ending" or (end and end - now < timedelta(hours=48)) or \
            (p.get("budget_total") and rem is not None and rem < 0.15 * p["budget_total"]):
        left = f"ends {end:%a %d %b}" if end and end - now < timedelta(hours=48) else "under 15% budget left"
        return "ending", f"Ending soon ({left})"
    return "open", None


def _campaigns_for(streamer: dict, decided: dict) -> list[tuple[dict, float, str]]:
    """Campaigns for this streamer: confirmed links first, then confident name matches (not yet linked)."""
    out = {}
    for p in db.q("""SELECT p.* FROM programs p JOIN streamer_programs sp ON sp.program_id=p.id
                     WHERE sp.streamer_id=? AND sp.confirmed=1 AND p.active=1""", (streamer["id"],)):
        if decided.get(p["id"]) != "rejected":
            out[p["id"]] = (p, 1.0, "Matched")
    if not out and decided.get(None) != "none":
        for p in db.q("SELECT * FROM programs WHERE active=1 AND streamer_label IS NOT NULL"):
            if decided.get(p["id"]) == "rejected":
                continue
            conf, why = _score(streamer, p)
            # Joined campaigns need a sure match; a campaign you haven't joined is only a suggestion,
            # so a likely match is enough (clicking the streamer asks you to confirm it).
            if conf >= AUTO_LINK or (not p.get("joined") and conf >= SUGGEST):
                out[p["id"]] = (p, conf, why)
    return list(out.values())


def _best_projection(streamer: dict, prog: dict, cpd: float) -> dict | None:
    best = None
    for plat in forecast.allowed_platforms(prog):
        cached = db.q1("""SELECT * FROM projections WHERE streamer_id=? AND program_id=? AND platform=?
                          AND computed_at >= datetime('now','-30 minutes')""", (streamer["id"], prog["id"], plat))
        if cached:
            pj = {"platform": plat, "per_clip": [cached["per_clip_low"], cached["per_clip_likely"],
                                                 cached["per_clip_high"]],
                  "per_day": [cached["per_day_low"], cached["per_day_likely"], cached["per_day_high"]],
                  "aud_per_pc_hour": cached["aud_per_pc_hour"],
                  "rank_value": (json.loads(cached["details"] or "{}")).get("rank_value", cached["per_clip_likely"]),
                  "warnings": json.loads(cached["warnings"] or "[]")}
        else:
            p = forecast.project(streamer, prog, plat, cpd)
            forecast.save(p)
            pj = {k: p[k] for k in ("platform", "per_clip", "per_day", "aud_per_pc_hour", "rank_value", "warnings")}
        if best is None or pj["rank_value"] > best["rank_value"]:
            best = pj
    return best


def reason_line(prog: dict, pj: dict | None, state: str) -> str:
    """One-line why, e.g. "Pays $2/1K, low competition, ends in 5 days"."""
    bits = []
    if prog.get("campaign_type") == "prize_pool":
        bits.append(f"${prog.get('pool_size') or 0:,.0f} prize pool")
    elif prog.get("payout_per_1k"):
        bits.append(f"Pays ${float(prog['payout_per_1k']):g}/1K")
    c = prog.get("clippers_joined")
    if c is not None:
        bits.append("low competition" if c < 50 else "busy" if c > 300 else f"{c} clippers")
    if prog.get("ends_at"):
        try:
            days = (datetime.fromisoformat(prog["ends_at"]) - _now()).days
            bits.append(f"ends in {days} day{'s' if days != 1 else ''}" if 0 <= days <= 14 else "")
        except ValueError:
            pass
    if state == "ending":
        bits.append("ending soon")
    return ", ".join(b for b in bits if b) or prog.get("name", "")


def grouped(game_id: int | None, streamers: list[dict] | None = None) -> dict:
    """Group streamers (already ranked for a game, or given) into joined / worth_joining / no_campaign."""
    if streamers is None:
        from ..discovery.service import ranked_streamers
        streamers = ranked_streamers(game_id) if game_id else []
    lim = autopilot_settings()
    min_clip = float(lim["min_projected_aud_per_clip"])
    cpd = float(lim["clips_per_streamer_per_day"])
    joined, worth, none_, left_out = [], [], [], []

    for s in streamers:
        decided = _decisions(s["id"])
        best_row = None
        for prog, conf, why in _campaigns_for(s, decided):
            state, excl = campaign_state(prog)
            pj = _best_projection(s, prog, cpd) if state not in ("ended",) else None
            row = {**{k: s.get(k) for k in s.keys()}, "campaign": _short(prog), "match_confidence": conf,
                   "match_reason": why, "projection": pj, "state": state, "excluded_reason": excl,
                   "reason": reason_line(prog, pj, state) + ("" if conf >= AUTO_LINK else " (check it's them)"),
                   "sort_value": (pj or {}).get("rank_value", 0) or 0}
            key = (bool(prog.get("joined")) and state != "ended", row["sort_value"])
            if best_row is None or key > best_row[0]:
                best_row = (key, row)
        if best_row is None:
            none_.append({**dict(s), "campaign": None, "projection": None, "sort_value": 0,
                          "reason": "Not paying clippers", "state": "none"})
            continue
        row = best_row[1]
        prog = row["campaign"]
        if prog["joined"] and row["state"] not in ("ended",):
            joined.append(row)
        elif prog["joined"]:
            none_.append({**row, "reason": "Campaign has ended"})
        elif row["state"] != "open":
            left_out.append(row)
        elif row["sort_value"] < min_clip:
            left_out.append({**row, "excluded_reason": f"Projects under your minimum ${min_clip:.2f} per clip"})
        else:
            worth.append(row)

    by_value = lambda r: (-(r.get("sort_value") or 0), -(r.get("profit_score") or 0))  # noqa: E731
    joined.sort(key=by_value)
    worth.sort(key=by_value)
    left_out.sort(key=by_value)
    none_.sort(key=lambda r: -(r.get("profit_score") or 0))
    return {"joined": joined, "worth_joining": worth, "worth_joining_left_out": left_out, "no_campaign": none_,
            "min_aud_per_clip": min_clip}


def _short(p: dict) -> dict:
    from .matching import _brief
    return _brief(p)
