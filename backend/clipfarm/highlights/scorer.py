"""Combine signals into a single excitement curve, pick peaks, turn them into clip windows."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .signals import smooth, zscore

SIGNAL_WEIGHTS = {"audio": 0.35, "chat": 0.30, "emote": 0.20, "viewers": 0.05, "speech": 0.10}


@dataclass
class Moment:
    start: float
    end: float
    peak: float
    score: float                      # 0..1
    signals: dict = field(default_factory=dict)


def _norm(z: np.ndarray) -> np.ndarray:
    """Map z-scores to 0..1 (z=0 -> ~0.3, z=3 -> ~0.95)."""
    return 1 / (1 + np.exp(-(z - 0.8)))


def composite(series: dict[str, np.ndarray]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """series keys from SIGNAL_WEIGHTS; missing/empty signals are skipped and weights renormalised."""
    present = {k: v for k, v in series.items() if v is not None and v.size and np.any(v != v[0])}
    if not present:
        n = max((v.size for v in series.values() if v is not None), default=0)
        return np.zeros(n), {}
    n = min(v.size for v in present.values())
    normed = {k: _norm(zscore(smooth(v[:n].astype(np.float32), 3))) for k, v in present.items()}
    total_w = sum(SIGNAL_WEIGHTS[k] for k in normed)
    curve = sum(SIGNAL_WEIGHTS[k] * v for k, v in normed.items()) / total_w
    return curve, normed


def pick_moments(curve: np.ndarray, normed: dict[str, np.ndarray], *, min_len: float = 15,
                 max_len: float = 60, pad_before: float = 3, pad_after: float = 2,
                 threshold: float = 0.55, top_n_per_hour: int = 4, min_gap: float = 45) -> list[Moment]:
    """Greedy peak picking with non-maximum suppression, then window shaping.

    A window spans the contiguous region where the curve stays above ~70% of the peak,
    plus padding, clamped to [min_len, max_len].
    """
    n = curve.size
    if n == 0:
        return []
    budget = max(1, int(round(top_n_per_hour * n / 3600))) if n > 600 else top_n_per_hour
    order = np.argsort(curve)[::-1]
    taken: list[Moment] = []
    for idx in order:
        val = float(curve[idx])
        if val < threshold or len(taken) >= budget:
            break
        if any(m.start - min_gap <= idx <= m.end + min_gap for m in taken):
            continue
        cut = val * 0.7
        lo = idx
        while lo > 0 and curve[lo - 1] >= cut and idx - lo < max_len:
            lo -= 1
        hi = idx
        while hi < n - 1 and curve[hi + 1] >= cut and hi - idx < max_len:
            hi += 1
        start, end = lo - pad_before, hi + 1 + pad_after
        length = end - start
        if length < min_len:                     # grow around the peak, biased to lead-up context
            extra = min_len - length
            start -= extra * 0.65
            end += extra * 0.35
        if end - start > max_len:                # trim around the peak
            start = max(start, idx - max_len * 0.6)
            end = start + max_len
        start, end = max(0.0, start), min(float(n), end)
        sig = {k: round(float(v[idx]), 3) for k, v in normed.items()}
        taken.append(Moment(round(start, 2), round(end, 2), float(idx), round(val, 3), sig))
    return sorted(taken, key=lambda m: m.start)


def boost_with_clip_views(moments: list[Moment], clip_offsets: list[tuple[float, int]]) -> None:
    """If community clips exist near a moment, raise its score (viewers already voted it's good)."""
    for m in moments:
        views = sum(v for off, v in clip_offsets if m.start - 30 <= off <= m.end + 30)
        if views:
            m.score = round(min(1.0, m.score + min(0.25, np.log10(views + 1) / 20)), 3)
            m.signals["clip_views"] = views
