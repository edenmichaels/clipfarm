"""Score one clip end to end (virality + projected earnings + rule flags) and store it on the clip."""
from __future__ import annotations

import json

from .. import db
from . import profit, rules_check, virality

PLATFORM_OF = rules_check.PRESET_PLATFORM


def score_clip(cid: int, edl: dict | None = None) -> dict:
    c = db.q1("SELECT * FROM clips WHERE id=?", (cid,))
    if not c:
        raise KeyError(cid)
    if edl is None:
        from ..edit import project
        p = project.get(cid)
        if not p:
            return {}
        edl = p["edl"]
    preset = edl["export"].get("preset") or "tiktok"
    w = virality.weights_for(c.get("streamer_id"), c.get("game_id"))
    st = db.q1("""SELECT AVG(viewers) v FROM streamer_snapshots WHERE streamer_id=?
                  AND taken_at >= datetime('now','-7 days')""", (c["streamer_id"],)) if c.get("streamer_id") else None
    e = {**edl, "_streamer_viewers": (st or {}).get("v") or 0}
    v = virality.score(e, w, virality.trends_list(), preset)
    pr = profit.project(c, v["score"], PLATFORM_OF.get(preset, "tiktok"))
    flags = rules_check.check(edl)
    lo, li, hi = pr["aud"] if pr else (None, None, None)
    db.ex("""UPDATE clips SET virality=?, virality_reason=?, virality_parts=?, profit_low=?, profit_likely=?,
             profit_high=?, profit_platform=?, expected_views=?, rule_flags=?, scored_at=datetime('now') WHERE id=?""",
          (v["score"], v["reason"], json.dumps(v["parts"]), lo, li, hi, pr["platform"] if pr else None,
           pr["expected_views"] if pr else None, json.dumps(flags), cid))
    return {"virality": v, "profit": pr, "flags": flags}


def rank_for_posting(clips: list[dict]) -> list[dict]:
    """Projected earnings first (rule errors last), then virality."""
    def key(c):
        flags = c.get("rule_flags") or []
        if isinstance(flags, str):
            flags = json.loads(flags)
        blocked = any(f.get("severity") == "error" for f in flags)
        return (blocked, -(c.get("profit_likely") or 0), -(c.get("virality") or 0))
    return sorted(clips, key=key)
