"""Projected earnings for one clip: expected views from its virality score and the platform's history,
pushed through the campaign's payout, caps and minimum views (the existing Monte-Carlo forecast)."""
from __future__ import annotations

import math

from .. import db
from ..campaigns import forecast as F


def view_multiplier(virality: int) -> float:
    """50 -> 1x (a typical clip), 90 -> ~3.5x (90+ clips break out more often), 20 -> ~0.43x."""
    return round(math.pow(4.0, (virality - 50) / 50) * (1.15 if virality >= 90 else 1.0), 3)


def project(clip: dict, virality: int, platform: str = "tiktok") -> dict | None:
    prog = db.q1("SELECT * FROM programs WHERE id=?", (clip.get("program_id"),)) if clip.get("program_id") else None
    streamer = db.q1("SELECT * FROM streamers WHERE id=?", (clip.get("streamer_id"),)) if clip.get("streamer_id") else None
    if not prog or not streamer:
        return None
    plats = F.allowed_platforms(prog)
    if platform not in plats:
        platform = plats[0] if plats else platform
    x = F.inputs_for(streamer, prog, platform, 1.0)
    base_median = F._median_views(x, x.day)
    x.median = (x.median or F.PLATFORMS[platform]["median"]) * view_multiplier(virality)
    sim = F.simulate(x, seed=F._seed("clip", clip.get("id"), virality, platform))
    lo, li, hi = F.band(sim["per_clip"])
    return {"platform": platform, "aud": [lo, li, hi], "rank_value": round(F.rank_value(sim["per_clip"]), 3),
            "expected_views": int(F._median_views(x, x.day)), "base_views": int(base_median), "multiplier": view_multiplier(virality),
            "campaign": prog["name"], "warnings": x.warnings}
