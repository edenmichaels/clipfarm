"""'Fix my clip': the one-click clean-up the Editor's simple mode runs.

Removes filler words and long silences, trims a slow start and a long tail after the payoff, keeps the
clip to a good length for the platform, picks a hook if none is set, and fixes campaign rules.
"""
from __future__ import annotations

from ..edit import timeline as T
from ..edit import transcript as tr
from . import rules_check, virality


def fix_my_clip(edl: dict) -> tuple[dict, list[str]]:
    done: list[str] = []
    e = edl
    a, b = e["range"]
    words = sorted(e["captions"]["words"], key=lambda w: w["start"])
    peak = e["moment"].get("peak")
    inside = [w for w in words if a <= w["start"] < b]
    # slow start: begin just before the first word, unless the payoff is right at the start
    if inside and inside[0]["start"] - a > 1.2 and (peak is None or peak - a > 3):
        a = round(max(a, inside[0]["start"] - 0.3), 3)
        done.append("trimmed the slow start")
    # long tail after the payoff
    if peak is not None and b - peak > 6:
        last = max((w["end"] for w in inside if w["start"] < peak + 4), default=peak)
        b = round(max(peak + 3.0, min(last + 0.5, peak + 5.0)), 3)
        done.append("cut the ending after the payoff")
    # too long for the platform: drop build-up from the front, keep 1–3 s before the build-up of the payoff
    lo, hi = virality.BEST_LEN.get(e["export"].get("preset") or "tiktok", (15, 40))
    if b - a > hi + 8 and peak is not None:
        a = round(max(a, peak - (hi - 6)), 3)
        done.append(f"shortened to about {hi} s")
    if [a, b] != e["range"]:
        e = tr.set_range(e, a, b)
    if tr.filler_indices(e):
        e = tr.remove_fillers(e)
        done.append("removed filler words")
    if tr.silences(e):
        e = tr.remove_silences(e)
        done.append("removed silences")
    if not (e["hook"].get("text") or "").strip() and e["hook"].get("options"):
        e = {**e, "hook": {**e["hook"], "text": e["hook"]["options"][0]}}
        done.append("added a hook")
    e, fixed = rules_check.fix_all(e)
    if fixed:
        done.append("fixed campaign rules")
    if T.duration(e) < 3:                                     # never destroy a clip
        return edl, []
    return e, done
