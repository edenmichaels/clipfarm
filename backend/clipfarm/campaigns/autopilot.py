"""Decision engine + auto-pilot.

v0.8.9: auto-pilot runs on the Live Watcher (live/pilot.py): this module ranks campaign streamers (below); the
watcher picks the best LIVE ones every minute and reads their chat. Auto-pilot no longer scans past VODs.

Every tick (30 min while auto-pilot is on):
  1. update campaign status: ended (past end date), out_of_budget, ending (< 48 h or < 15 % budget left)
  2. recompute projections for every linked streamer x active campaign x allowed platform
  3. rank by projected AUD per hour of PC time (risk-adjusted, so per-view campaigns beat pools unless a pool
     really projects higher), drop anything under the user's minimum $/clip
  4. pick streamers, clips per streamer and platforms within the limits:
     max clips/day, max posts per account per day, minimum projected $ per clip
  5. compare with the last plan and log every switch with its reason
  6. point the "Auto-pilot" project at the chosen streamers with their clip quotas
Streamers without an active campaign are never chosen ("no payout").
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .. import db
from . import forecast
from .matching import sync_permissions

DEFAULTS = {"enabled": False, "max_clips_per_day": 20, "max_posts_per_account_per_day": 3,
            "min_projected_aud_per_clip": 0.50, "clips_per_streamer_per_day": 4, "tick_minutes": 30,
            # v0.8.9 live auto-pilot
            "max_streamers": 3, "min_score": 7.0, "clips_per_streamer_per_hour": 6, "campaigns": [], "run_hours": None}
PROJECT_NAME = "Auto-pilot"


def settings() -> dict:
    return {**DEFAULTS, **(db.get_setting("autopilot") or {})}


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def log(action: str, reason: str, *, from_program: int | None = None, to_program: int | None = None,
        streamer_id: int | None = None) -> None:
    db.ex("INSERT INTO autopilot_log(action, from_program_id, to_program_id, streamer_id, reason) VALUES (?,?,?,?,?)",
          (action, from_program, to_program, streamer_id, reason))
    db.log("info", "autopilot", reason)


def refresh_status() -> list[str]:
    """Flag campaigns that are ending, out of budget or over. Returns messages for what changed."""
    changed = []
    now = now_utc()
    for p in db.q("SELECT * FROM programs WHERE active=1 AND joined=1"):
        status = p["status"] or "active"
        new = "active"
        if p["ends_at"] and datetime.fromisoformat(p["ends_at"]) <= now:
            new = "ended"
        elif p["campaign_type"] != "prize_pool" and p["budget_remaining"] is not None and \
                p["budget_remaining"] <= max(1.0, p["min_payout"] or 0):
            new = "out_of_budget"
        elif (p["ends_at"] and datetime.fromisoformat(p["ends_at"]) - now < timedelta(hours=48)) or \
                (p["budget_total"] and p["budget_remaining"] is not None
                 and p["budget_remaining"] < 0.15 * p["budget_total"]):
            new = "ending"
        if status == "ended" and new != "ended":
            continue        # an ended campaign never comes back by itself
        if new != status:
            db.ex("UPDATE programs SET status=? WHERE id=?", (new, p["id"]))
            msg = {"ended": f"{p['name']} has ended.", "out_of_budget": f"{p['name']} is out of budget.",
                   "ending": f"{p['name']} is ending soon (under 48 h or 15 % budget left).",
                   "active": f"{p['name']} is active again."}[new]
            changed.append(msg)
            log("status", msg, from_program=p["id"])
    return changed


def plan(limits: dict | None = None) -> list[dict]:
    """Choose streamers, clip counts and platforms. Pure decision over the projections table."""
    lim = {**settings(), **(limits or {})}
    projections = forecast.project_all()
    accounts = db.q("SELECT platform, COUNT(*) n FROM accounts GROUP BY platform")
    capacity = {a["platform"]: a["n"] * lim["max_posts_per_account_per_day"] for a in accounts}
    rows = [p for p in projections if p["per_clip"][1] >= lim["min_projected_aud_per_clip"]]
    best: dict[int, dict] = {}
    for p in rows:          # best campaign per streamer, then its best platforms
        cur = best.get(p["streamer_id"])
        if not cur or p["aud_per_pc_hour"] > cur["aud_per_pc_hour"]:
            best[p["streamer_id"]] = p
    chosen, budget = [], int(lim["max_clips_per_day"])
    for p in sorted(best.values(), key=lambda r: r["aud_per_pc_hour"], reverse=True):
        if budget <= 0:
            break
        plats = [q for q in rows if q["streamer_id"] == p["streamer_id"] and q["program_id"] == p["program_id"]]
        plats.sort(key=lambda q: q["aud_per_pc_hour"], reverse=True)
        usable = [q["platform"] for q in plats if capacity.get(forecast.ACCOUNT_PLATFORM[q["platform"]], 0) > 0]
        if not usable:
            continue
        n = min(int(lim["clips_per_streamer_per_day"]), budget)
        for plat in usable:
            capacity[forecast.ACCOUNT_PLATFORM[plat]] -= n
        budget -= n
        prog = db.q1("SELECT name, payout_per_1k, campaign_type, pool_size FROM programs WHERE id=?", (p["program_id"],))
        chosen.append({"streamer_id": p["streamer_id"], "program_id": p["program_id"], "program": prog["name"],
                       "rate": prog["payout_per_1k"], "campaign_type": prog["campaign_type"],
                       "clips_per_day": n, "platforms": usable, "aud_per_pc_hour": p["aud_per_pc_hour"],
                       "per_clip_likely": p["per_clip"][1]})
    return chosen


def _describe(c: dict) -> str:
    if c["campaign_type"] == "prize_pool":
        return f"{c['program']} (prize pool)"
    return f"{c['program']} (${c['rate']:.2f}/1k)" if c.get("rate") else c["program"]


def diff_and_log(old: list[dict], new: list[dict]) -> None:
    old_by = {c["streamer_id"]: c for c in old}
    new_by = {c["streamer_id"]: c for c in new}
    dropped = [c for sid, c in old_by.items() if sid not in new_by]
    added = [c for sid, c in new_by.items() if sid not in old_by]
    for c in new:
        o = old_by.get(c["streamer_id"])
        if o and o["program_id"] != c["program_id"]:
            log("switch", f"Switched {_name(c['streamer_id'])} from {_describe(o)} to {_describe(c)}: "
                          f"A${c['aud_per_pc_hour']:.2f}/PC-hour vs A${o['aud_per_pc_hour']:.2f}",
                from_program=o["program_id"], to_program=c["program_id"], streamer_id=c["streamer_id"])
    # Pair a dropped streamer with the one that replaced it so the log reads as a switch.
    for d, a in zip(sorted(dropped, key=lambda c: c["aud_per_pc_hour"]), sorted(added, key=lambda c: -c["aud_per_pc_hour"])):
        why = _drop_reason(d)
        rate = (f": {_name(a['streamer_id'])} pays ${a['rate']:.2f}/1k vs ${d['rate']:.2f}/1k"
                if a.get("rate") and d.get("rate") else f": A${a['aud_per_pc_hour']:.2f} vs "
                                                        f"A${d['aud_per_pc_hour']:.2f} per PC-hour")
        log("switch", f"Switched from {_name(d['streamer_id'])} ({_describe(d)}) to {_name(a['streamer_id'])} "
                      f"({_describe(a)}){rate}{why}", from_program=d["program_id"], to_program=a["program_id"],
            streamer_id=a["streamer_id"])
    for d in dropped[len(added):]:
        log("drop", f"Stopped clipping {_name(d['streamer_id'])} ({_describe(d)}){_drop_reason(d) or ''}",
            from_program=d["program_id"], streamer_id=d["streamer_id"])
    for a in added[len(dropped):]:
        log("select", f"Started clipping {_name(a['streamer_id'])} for {_describe(a)}: projected "
                      f"A${a['per_clip_likely']:.2f}/clip, {a['clips_per_day']} clips/day on "
                      f"{', '.join(a['platforms'])}", to_program=a["program_id"], streamer_id=a["streamer_id"])


def _drop_reason(c: dict) -> str:
    p = db.q1("SELECT status, name FROM programs WHERE id=?", (c["program_id"],))
    if p and p["status"] in ("ended", "out_of_budget"):
        return f" ({p['name']} is {'over' if p['status'] == 'ended' else 'out of budget'})"
    return ""


def _name(sid: int) -> str:
    s = db.q1("SELECT display_name FROM streamers WHERE id=?", (sid,))
    return s["display_name"] if s else f"streamer {sid}"


def tick() -> dict:
    s = settings()
    msgs = refresh_status()
    sync_permissions()
    chosen = plan(s)
    old = db.get_setting("autopilot_plan") or []
    diff_and_log(old, chosen)
    db.set_setting("autopilot_plan", chosen)
    pid = None
    if s["enabled"]:                     # v0.8.9: the Live Watcher does the work; the old VOD project is paused
        old_p = db.q1("SELECT id FROM projects WHERE name=? AND status='running'", (PROJECT_NAME,))
        if old_p:
            from ..jobs.engine import pause_project
            pause_project(old_p["id"])
            log("pause", "Auto-pilot now watches streamers live; the old past-stream project was paused.")
        try:
            from ..live import watcher
            watcher.manager().start()
        except Exception as e:           # noqa: BLE001
            log("error", f"Live Watcher didn't start: {e}")
    return {"status_changes": msgs, "plan": chosen, "project_id": pid}


def quota_left(project: dict, streamer_id: int) -> int | None:
    """Clips still allowed today for this streamer in an auto-pilot project (None = no quota)."""
    q = json.loads(project["output_settings"]).get("quotas", {}).get(str(streamer_id))
    if not q:
        return None
    made = db.q1("""SELECT COUNT(*) n FROM clips WHERE project_id=? AND streamer_id=?
                    AND created_at >= datetime('now','start of day')""", (project["id"], streamer_id))["n"]
    queued = db.q1("""SELECT COUNT(*) n FROM jobs WHERE project_id=? AND kind='render_moment'
                      AND status IN ('queued','running') AND json_extract(payload,'$.streamer_id')=?""",
                   (project["id"], streamer_id))["n"]
    return max(0, int(q["clips_per_day"]) - made - queued)
