"""Auto-pilot on the Live Watcher (v0.8.9).

Every minute while auto-pilot is on (and inside your run hours):
  1. candidates = streamers in your campaigns (all joined ones, or the ones you ticked), permitted, on Twitch,
     ranked by projected A$: pay-per-view campaigns first, then A$ per hour of PC time
  2. which of them are live right now (one Get Streams call)
  3. keep watching the ones still live, and fill free slots with the best live ones, up to "max streamers at
     once" (default 3; lowered automatically when the CPU is busy or the disk is nearly full)
  4. stop a watcher when its streamer goes offline and move to the next-best live streamer
Everything it does goes in the auto-pilot log.
"""
from __future__ import annotations

import time

from .. import db
from . import status as live_status

DEFAULTS = {"max_streamers": 3, "min_score": 7.0, "clips_per_streamer_per_hour": 6, "campaigns": [],
            "run_hours": None}                     # run_hours: {"start": "22:00", "end": "07:00"} or None (always)
CPU_HIGH, DISK_LOW_GB = 85.0, 5.0


def settings() -> dict:
    from ..campaigns import autopilot
    s = autopilot.settings()
    return {**DEFAULTS, **{k: s[k] for k in DEFAULTS if k in s}, "enabled": bool(s.get("enabled"))}


def in_run_hours(rh: dict | None, now: float | None = None) -> bool:
    if not rh or not rh.get("start") or not rh.get("end"):
        return True
    lt = time.localtime(now or time.time())
    cur = lt.tm_hour * 60 + lt.tm_min
    sh, sm = (int(x) for x in rh["start"].split(":"))
    eh, em = (int(x) for x in rh["end"].split(":"))
    a, b = sh * 60 + sm, eh * 60 + em
    return a <= cur < b if a < b else (cur >= a or cur < b)        # overnight windows wrap past midnight


def capacity(max_streamers: int, cpu: float | None = None, disk_free_gb: float | None = None) -> tuple[int, str | None]:
    """How many watchers to run now, and why it's lower than the maximum (if it is)."""
    if cpu is None or disk_free_gb is None:
        import psutil
        from ..jobs import storage
        cpu = psutil.cpu_percent(interval=None) if cpu is None else cpu
        if disk_free_gb is None:
            try:
                disk_free_gb = psutil.disk_usage(str(storage.media_dir())).free / 1024 ** 3
            except OSError:
                disk_free_gb = 999.0
    n, why = max(1, int(max_streamers)), None
    if disk_free_gb < DISK_LOW_GB:
        n, why = 1, f"disk nearly full ({disk_free_gb:.1f} GB free)"
    elif cpu >= CPU_HIGH:
        n, why = max(1, n - 1), f"CPU busy ({cpu:.0f}%)"
    return n, why


def candidates() -> list[dict]:
    """Campaign streamers worth watching, best first (pay-per-view first, then projected A$)."""
    from .. import compliance
    from ..campaigns import autopilot
    s = settings()
    plan = db.get_setting("autopilot_plan") or autopilot.plan()
    want = {int(x) for x in s["campaigns"]}
    out, seen = [], set()
    for c in sorted(plan, key=lambda c: (c.get("campaign_type") != "pay_per_view", -(c.get("aud_per_pc_hour") or 0))):
        if c["streamer_id"] in seen or (want and c["program_id"] not in want):
            continue
        st = db.q1("SELECT * FROM streamers WHERE id=?", (c["streamer_id"],))
        if not st or st["platform"] != "twitch" or not compliance.can_clip(st["id"])[0]:
            continue
        seen.add(st["id"])
        out.append({**c, "display_name": st["display_name"]})
    return out


def _rate(c: dict) -> str:
    if c.get("campaign_type") == "prize_pool":
        return "prize pool"
    return f"${c['rate']:.2f}/1K" if c.get("rate") else (c.get("program") or "")


def step(mgr, now: float | None = None, status_fn=None, cap=None) -> list[int]:
    """Pick who to watch now. Writes live_watcher.auto_streamers and logs every start/stop."""
    from ..campaigns import autopilot
    from . import watcher as W
    s = settings()
    cur = [int(x) for x in W.settings()["auto_streamers"]]
    target: list[int] = []
    why_low = None
    if s["enabled"] and in_run_hours(s["run_hours"], now):
        cands = candidates()
        st = (status_fn or live_status.for_streamers)([c["streamer_id"] for c in cands]) if cands else {}
        live = [c for c in cands if (st.get(c["streamer_id"]) or {}).get("state") == "live"]
        n, why_low = (int(cap), None) if cap is not None else capacity(int(s["max_streamers"]))
        still = [c["streamer_id"] for c in live if c["streamer_id"] in cur][:n]       # don't hop while they're live
        rest = [c["streamer_id"] for c in live if c["streamer_id"] not in still]
        target = still + rest[: n - len(still)]
        info = {c["streamer_id"]: c for c in cands}
        for sid in target:
            if sid not in cur:
                c = info[sid]
                v = (st.get(sid) or {}).get("viewers")
                autopilot.log("watch", f"Started watching {c['display_name']} (live{f', {v:,} viewers' if v else ''}, "
                                       f"{_rate(c)})", streamer_id=sid, to_program=c.get("program_id"))
        if why_low and len(target) < int(s["max_streamers"]) and len(live) > len(target):
            db.set_setting("autopilot_capacity_note", f"Watching {len(target)} instead of {s['max_streamers']}: {why_low}")
        else:
            db.set_setting("autopilot_capacity_note", None)
    for sid in cur:
        if sid not in target:
            w = mgr.watches.get(sid) if mgr else None
            sess = w.session if w else {"triggers": 0, "kept": 0}
            name = (db.q1("SELECT display_name FROM streamers WHERE id=?", (sid,)) or {}).get("display_name", sid)
            reason = "offline" if s["enabled"] and in_run_hours(s["run_hours"], now) else \
                ("outside run hours" if s["enabled"] else "auto-pilot off")
            autopilot.log("unwatch", f"Stopped watching {name} ({reason}) · Kept {sess['kept']} of "
                                     f"{sess['triggers']} triggers", streamer_id=sid)
    if target != cur:
        W.save_settings(auto_streamers=target)
    return target


def status() -> dict:
    s = settings()
    from . import watcher as W
    return {"enabled": s["enabled"], "watching": W.settings()["auto_streamers"], "max_streamers": s["max_streamers"],
            "in_run_hours": in_run_hours(s["run_hours"]), "run_hours": s["run_hours"],
            "note": db.get_setting("autopilot_capacity_note")}
