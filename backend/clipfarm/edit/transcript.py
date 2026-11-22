"""Transcript editing: delete words to cut the video, remove fillers / silences, change the range, fix words."""
from __future__ import annotations

import copy
import re

from ..editing.captions import FILLERS

PAD = 0.04            # keep a sliver either side of a cut so neighbours aren't clipped
SILENCE = 0.6         # gaps longer than this count as silence
KEEP_GAP = 0.25       # how much of a silence to keep (so speech doesn't feel jammed)


def _merge(cuts: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted(cuts):
        if out and a <= out[-1][1] + 0.02:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in out]


def delete_words(edl: dict, indices: list[int]) -> dict:
    """Mark words deleted and cut their time out of the video."""
    e = copy.deepcopy(edl)
    ws = e["captions"]["words"]
    idx = sorted(set(i for i in indices if 0 <= i < len(ws)))
    for i in idx:
        ws[i]["deleted"] = True
    cuts = list(e.get("cuts", []))
    for i in idx:
        w = ws[i]
        prev_end = next((float(ws[j]["end"]) for j in range(i - 1, -1, -1) if not ws[j].get("deleted")), e["range"][0])
        next_start = next((float(ws[j]["start"]) for j in range(i + 1, len(ws)) if not ws[j].get("deleted")), e["range"][1])
        a = max(prev_end + PAD, float(w["start"]) - PAD)
        b = min(next_start - PAD, float(w["end"]) + PAD)
        # deleting the gap after a deleted word too, so consecutive deletions join up
        if i + 1 < len(ws) and ws[i + 1].get("deleted"):
            b = max(b, float(ws[i + 1]["start"]))
        if b > a:
            cuts.append([a, b])
    e["cuts"] = _merge(cuts)
    return e


def restore_words(edl: dict, indices: list[int]) -> dict:
    """Undo a word deletion (removes the cut that covered it)."""
    e = copy.deepcopy(edl)
    ws = e["captions"]["words"]
    for i in indices:
        if 0 <= i < len(ws):
            ws[i].pop("deleted", None)
            s, t = float(ws[i]["start"]), float(ws[i]["end"])
            e["cuts"] = [c for c in e.get("cuts", []) if not (c[0] < t and c[1] > s)]
    return e


def filler_indices(edl: dict) -> list[int]:
    return [i for i, w in enumerate(edl["captions"]["words"])
            if not w.get("deleted") and re.sub(r"[^\w']", "", w["word"]).lower() in FILLERS]


def remove_fillers(edl: dict) -> dict:
    return delete_words(edl, filler_indices(edl))


def silences(edl: dict, min_gap: float = SILENCE) -> list[list[float]]:
    """Silent gaps (no live word) inside the range, minus a little breathing room."""
    a, b = edl["range"]
    ws = [w for w in edl["captions"]["words"] if not w.get("deleted") and float(w["end"]) > a and float(w["start"]) < b]
    pts = [(a, a)] + [(float(w["start"]), float(w["end"])) for w in ws] + [(b, b)]
    out = []
    for (_, e1), (s2, _) in zip(pts, pts[1:]):
        if s2 - e1 > min_gap:
            out.append([round(e1 + KEEP_GAP / 2, 3), round(s2 - KEEP_GAP / 2, 3)])
    return out


def remove_silences(edl: dict, min_gap: float = SILENCE) -> dict:
    e = copy.deepcopy(edl)
    e["cuts"] = _merge(list(e.get("cuts", [])) + silences(e, min_gap))
    return e


def set_range(edl: dict, start: float, end: float) -> dict:
    """Drag the clip's start/end on the transcript (clamped to the source we have)."""
    e = copy.deepcopy(edl)
    lo, hi = e["source"]["start"], e["source"]["end"]
    s, t = max(lo, float(start)), min(hi, float(end))
    if t - s < 1.0:
        raise ValueError("The clip has to be at least 1 second long.")
    e["range"] = [round(s, 3), round(t, 3)]
    return e


def fix_word(edl: dict, index: int, text: str) -> dict:
    e = copy.deepcopy(edl)
    text = text.strip()
    if not text:
        return delete_words(e, [index])
    e["captions"]["words"][index]["word"] = text
    e["captions"].pop("translations", None)        # translations are redone from the fixed words
    return e
