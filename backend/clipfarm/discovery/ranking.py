"""Clip Profit Score — pure functions so they're easy to test and tune.

score = w_a*audience + w_p*payout + w_c*(low competition) + w_h*highlight_freq + w_g*growth
All components are 0..1. Prohibited streamers always score 0.
Weights are deliberately simple; the "What's working" analytics (milestone 3) can refit them
from the user's own post performance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

WEIGHTS = {"audience": 0.30, "payout": 0.30, "competition": 0.15, "highlight": 0.15, "growth": 0.10}


@dataclass
class RankInput:
    avg_viewers: float
    followers: int | None
    payout_per_1k: float | None       # best active program for this streamer, None if none
    permission_status: str            # allowed | program_member | unknown | prohibited
    competition_count: int | None     # est. other clippers covering them (None = unknown)
    clips_per_stream_hour: float      # community clip rate — proxy for highlight frequency
    growth_pct: float                 # viewer change over the window, e.g. 0.12 = +12%


def audience_score(avg_viewers: float) -> float:
    """Log scale: 10 viewers≈0.2, 1k≈0.6, 100k≈1.0."""
    return max(0.0, min(1.0, math.log10(max(avg_viewers, 0) + 1) / 5))


def payout_score(payout_per_1k: float | None, permission_status: str) -> float:
    if payout_per_1k is None:
        # Allowed-but-unpaid still has value (growing your account), so not zero.
        return 0.15 if permission_status == "allowed" else 0.0
    return max(0.0, min(1.0, payout_per_1k / 3.0))   # $3/1k views or better = max


def competition_score(count: int | None) -> float:
    """1.0 = nobody else clipping them; decays as more clippers pile in. Unknown = neutral."""
    if count is None:
        return 0.5
    return 1.0 / (1.0 + count / 10.0)


def highlight_score(clips_per_hour: float) -> float:
    return max(0.0, min(1.0, clips_per_hour / 20.0))   # 20+ community clips/hour = very clippable


def growth_score(growth_pct: float) -> float:
    return max(0.0, min(1.0, 0.5 + growth_pct))      # flat = 0.5, +50% = 1.0


def earnings_tier(avg_viewers: float) -> str:
    """Coarse public-data ESTIMATE of streamer size. Never shown without the 'estimate' label."""
    if avg_viewers >= 20_000:
        return "Top tier (est.)"
    if avg_viewers >= 2_000:
        return "Large (est.)"
    if avg_viewers >= 200:
        return "Mid (est.)"
    return "Small (est.)"


def clip_profit_score(x: RankInput) -> dict:
    if x.permission_status == "prohibited":
        parts = dict.fromkeys(WEIGHTS, 0.0)
        return {"score": 0.0, **parts, "tier": earnings_tier(x.avg_viewers), "blocked": True}
    parts = {
        "audience": audience_score(x.avg_viewers),
        "payout": payout_score(x.payout_per_1k, x.permission_status),
        "competition": competition_score(x.competition_count),
        "highlight": highlight_score(x.clips_per_stream_hour),
        "growth": growth_score(x.growth_pct),
    }
    score = sum(WEIGHTS[k] * v for k, v in parts.items())
    if x.permission_status == "unknown":
        score *= 0.5   # unknown permission: shown, but ranked down and blocked from clipping by default
    return {"score": round(score * 100, 1), **{k: round(v, 3) for k, v in parts.items()},
            "tier": earnings_tier(x.avg_viewers), "blocked": False}
