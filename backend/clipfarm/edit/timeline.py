"""Source time <-> output time for an edit project.

The output is the source range [in, out] minus the cuts, played back to back. Everything that has to
survive edits (words, zooms, the moment's peak, layout keyframes) is stored in SOURCE time; this module
maps it onto the output timeline.
"""
from __future__ import annotations

MIN_SEG = 0.08     # drop slivers shorter than this


def kept(edl: dict) -> list[tuple[float, float]]:
    a, b = edl["range"]
    cuts = sorted((max(a, float(x)), min(b, float(y))) for x, y in edl.get("cuts", []) if y > a and x < b)
    out, cur = [], a
    for x, y in cuts:
        if x > cur:
            out.append((cur, x))
        cur = max(cur, y)
    if b > cur:
        out.append((cur, b))
    return [(round(x, 3), round(y, 3)) for x, y in out if y - x >= MIN_SEG]


def duration(edl: dict) -> float:
    return round(sum(y - x for x, y in kept(edl)), 3)


def to_out(edl: dict, t: float) -> float | None:
    """Output time of source time t (None if t was cut)."""
    acc = 0.0
    for x, y in kept(edl):
        if x <= t <= y:
            return round(acc + (t - x), 3)
        acc += y - x
    return None


def to_src(edl: dict, t: float) -> float:
    acc = 0.0
    segs = kept(edl)
    for x, y in segs:
        if t <= acc + (y - x):
            return round(x + (t - acc), 3)
        acc += y - x
    return segs[-1][1] if segs else edl["range"][1]


def words_out(edl: dict, words: list[dict] | None = None) -> list[dict]:
    """Caption words on the output timeline; words inside cuts disappear."""
    out = []
    for w in (edl["captions"]["words"] if words is None else words):
        if w.get("deleted"):
            continue
        s, e = to_out(edl, float(w["start"])), to_out(edl, float(w["end"]))
        if s is None and e is None:
            continue
        if s is None or e is None:          # straddles a cut: keep the part that survives
            mid = to_out(edl, (float(w["start"]) + float(w["end"])) / 2)
            if mid is None:
                continue
            s = s if s is not None else mid
            e = e if e is not None else mid + 0.05
        out.append({**w, "start": s, "end": max(e, s + 0.05)})
    return out


def intervals_out(edl: dict, iv: list) -> list[tuple[float, float]]:
    """Map source-time intervals onto the output (split by cuts)."""
    res = []
    acc = 0.0
    for x, y in kept(edl):
        for a, b in iv:
            lo, hi = max(a, x), min(b, y)
            if hi > lo:
                res.append((round(acc + lo - x, 3), round(acc + hi - x, 3)))
        acc += y - x
    return sorted(res)
