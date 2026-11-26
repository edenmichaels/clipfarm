"""Source time <-> output time for an edit project.

The output is the source range [in, out] minus the cuts, played back to back -- with optional speed ramps
(a source interval played faster or slower), freeze frames (one source frame held for N seconds) and more
clips appended after it (a multi-clip sequence). Everything that has to survive edits (words, zooms, the
moment's peak, layout keyframes) is stored in SOURCE time; this module maps it onto the output timeline.

Segments: (item, src_a, src_b, speed, hold)
  item  0 = the main clip, 1.. = sequence items (edl["sequence"][item-1])
  play  : src_a < src_b, output length (src_b - src_a) / speed
  freeze: src_a == src_b, output length = hold
"""
from __future__ import annotations

from typing import NamedTuple

MIN_SEG = 0.08     # drop slivers shorter than this
SPEED_RANGE = (0.25, 4.0)


class Seg(NamedTuple):
    item: int
    a: float
    b: float
    speed: float = 1.0
    hold: float = 0.0

    @property
    def length(self) -> float:
        return self.hold if self.hold else (self.b - self.a) / self.speed


def _kept(rng, cuts) -> list[tuple[float, float]]:
    a, b = rng
    cs = sorted((max(a, float(x)), min(b, float(y))) for x, y in cuts or [] if y > a and x < b)
    out, cur = [], a
    for x, y in cs:
        if x > cur:
            out.append((cur, x))
        cur = max(cur, y)
    if b > cur:
        out.append((cur, b))
    return [(round(x, 3), round(y, 3)) for x, y in out if y - x >= MIN_SEG]


def kept(edl: dict) -> list[tuple[float, float]]:
    """The main clip's kept source ranges (cuts applied; speed and freezes don't change these)."""
    return _kept(edl["range"], edl.get("cuts", []))


def _item_segments(item: int, rng, cuts, speeds, freezes) -> list[Seg]:
    out: list[Seg] = []
    ramps = sorted(((float(r["a"]), float(r["b"]), max(SPEED_RANGE[0], min(SPEED_RANGE[1], float(r.get("speed", 1)))))
                    for r in speeds or [] if float(r["b"]) > float(r["a"])), key=lambda r: r[0])
    holds = sorted((float(f["t"]), max(0.1, min(10.0, float(f.get("dur", 1.0))))) for f in freezes or [])
    for x, y in _kept(rng, cuts):
        pts = sorted({x, y, *[p for r in ramps for p in r[:2] if x < p < y], *[t for t, _ in holds if x < t < y]})
        for p, q in zip(pts, pts[1:]):
            for t, d in holds:                      # a freeze sits at the start of the piece it begins
                if abs(t - p) < 1e-6 or (p == x and abs(t - x) < 1e-6):
                    out.append(Seg(item, p, p, 1.0, d))
            if q - p < MIN_SEG / 4:
                continue
            sp = next((s for a, b, s in ramps if a <= p + 1e-6 and q <= b + 1e-6), 1.0)
            out.append(Seg(item, round(p, 3), round(q, 3), sp))
        for t, d in holds:
            if abs(t - y) < 1e-6:
                out.append(Seg(item, y, y, 1.0, d))
    return out


def segments(edl: dict) -> list[Seg]:
    segs = _item_segments(0, edl["range"], edl.get("cuts"), edl.get("speed"), edl.get("freezes"))
    for i, it in enumerate(edl.get("sequence") or [], start=1):
        segs += _item_segments(i, it["range"], it.get("cuts"), it.get("speed"), it.get("freezes"))
    return segs


def duration(edl: dict) -> float:
    return round(sum(s.length for s in segments(edl)), 3)


def to_out(edl: dict, t: float, item: int = 0) -> float | None:
    """Output time of source time t of an item (None if t was cut)."""
    acc = 0.0
    for s in segments(edl):
        if s.item == item and not s.hold and s.a <= t <= s.b:
            return round(acc + (t - s.a) / s.speed, 3)
        acc += s.length
    return None


def to_src(edl: dict, t: float) -> float:
    """Main-clip source time at output time t (sequence items / freezes map to their nearest main source time)."""
    acc = 0.0
    last = edl["range"][0]
    for s in segments(edl):
        if s.item == 0:
            if t <= acc + s.length:
                return round(s.a if s.hold else s.a + (t - acc) * s.speed, 3)
            last = s.b
        acc += s.length
    return last


def item_offset(edl: dict, item: int) -> float:
    """Output time where a sequence item starts."""
    acc = 0.0
    for s in segments(edl):
        if s.item == item:
            return round(acc, 3)
        acc += s.length
    return round(acc, 3)


def words_out(edl: dict, words: list[dict] | None = None, item: int = 0) -> list[dict]:
    """Caption words on the output timeline; words inside cuts disappear. With item=0 and no `words`, the
    sequence items' words are appended too."""
    src = edl["captions"]["words"] if words is None else words
    out = []
    for w in src:
        if w.get("deleted"):
            continue
        s, e = to_out(edl, float(w["start"]), item), to_out(edl, float(w["end"]), item)
        if s is None and e is None:
            continue
        if s is None or e is None:          # straddles a cut: keep the part that survives
            mid = to_out(edl, (float(w["start"]) + float(w["end"])) / 2, item)
            if mid is None:
                continue
            s = s if s is not None else mid
            e = e if e is not None else mid + 0.05
        out.append({**w, "start": s, "end": max(e, s + 0.05)})
    if words is None and item == 0:
        for i, it in enumerate(edl.get("sequence") or [], start=1):
            out += words_out(edl, it.get("words") or [], i)
    return out


def intervals_out(edl: dict, iv: list) -> list[tuple[float, float]]:
    """Map main-clip source-time intervals onto the output (split by cuts, scaled by speed)."""
    res = []
    acc = 0.0
    for s in segments(edl):
        if s.item == 0 and not s.hold:
            for a, b in iv:
                lo, hi = max(a, s.a), min(b, s.b)
                if hi > lo:
                    res.append((round(acc + (lo - s.a) / s.speed, 3), round(acc + (hi - s.a) / s.speed, 3)))
        acc += s.length
    return sorted(res)


def out_segments(edl: dict) -> list[dict]:
    """For the Editor: every segment with its output start/end."""
    acc, out = 0.0, []
    for s in segments(edl):
        out.append({"item": s.item, "a": s.a, "b": s.b, "speed": s.speed, "hold": s.hold,
                    "out_a": round(acc, 3), "out_b": round(acc + s.length, 3)})
        acc += s.length
    return out
