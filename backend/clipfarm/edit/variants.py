"""Clip variants for A/B tests: 2-3 versions of a strong moment with a different hook, length and layout.

Each variant is its own clip (with its own edit project, score and projection) that remembers which clip it
came from, so you can post them to different accounts and compare real views.
"""
from __future__ import annotations

import copy

from .. import db
from . import project
from . import timeline as T
from . import transcript as tr

LABELS = ["B", "C", "D"]
ALT_LAYOUT = {"split": "blur_fill", "blur_fill": "crop", "crop": "split", "speaker": "split", "pip": "split"}


def _tight(e: dict) -> dict:
    """Shorter: start closer to the payoff and end soon after it."""
    peak = e["moment"].get("peak")
    a, b = e["range"]
    if peak is None:
        return tr.set_range(e, a + (b - a) * 0.2, b)
    na = max(a, peak - 8.0)
    nb = min(b, peak + 3.0)
    return tr.set_range(e, na, nb) if nb - na >= 6 else e


def _long(e: dict) -> dict:
    """Longer build-up: start a few seconds earlier when the source has it."""
    a, b = e["range"]
    lo = e["source"].get("start", 0.0)
    return tr.set_range(e, max(lo, a - 5.0), b)


def _relayout(e: dict) -> dict:
    kfs = e["layout"].get("keyframes") or []
    if not kfs:
        return e
    first = kfs[0]["layout"]
    new = ALT_LAYOUT.get(first, "blur_fill")
    if new == "split" and not e["layout"].get("facecam"):
        new = "blur_fill"
    from .reframe import enforce_rules
    e["layout"]["keyframes"] = [{**k, "layout": new if k["layout"] == first else k["layout"], "auto": False} for k in kfs]
    e["layout"]["keyframes"], _ = enforce_rules(e["layout"]["keyframes"], e["rules"].get("onscreen") or [],
                                                e["layout"].get("facecam"))
    return e


def plan(edl: dict, n: int = 2) -> list[tuple[str, dict, str]]:
    """-> [(label, edl, what changed)]"""
    hooks = [h for h in (edl["hook"].get("options") or []) if h and h != edl["hook"].get("text")]
    out = []
    recipes = [("shorter, different hook", lambda e: _tight(e), 0),
               ("different layout, different hook", lambda e: _relayout(e), 1),
               ("longer build-up", lambda e: _long(e), 2)]
    for i, (what, fn, hi) in enumerate(recipes[:max(1, min(3, n))]):
        e = fn(copy.deepcopy(edl))
        if hooks:
            e["hook"]["text"] = hooks[hi % len(hooks)]
        if T.duration(e) < 3:
            continue
        out.append((LABELS[i], e, what))
    return out


def make(clip_id: int, n: int = 2) -> list[dict]:
    p = project.get(clip_id)
    if not p:
        raise KeyError(clip_id)
    c = db.q1("SELECT * FROM clips WHERE id=?", (clip_id,))
    root = c.get("variant_of") or clip_id
    made = []
    for label, e, what in plan(p["edl"], n):
        title = f"{(c['title'] or 'Clip').split(' [')[0]} [{label}]"
        cols = ["project_id", "moment_id", "streamer_id", "game_id", "program_id", "hook_text", "description", "hashtags",
                "layout", "caption_style", "review_status", "words", "caption_preset", "moment_type", "moment_reason",
                "source_path", "rules_applied", "caption_text", "needs_payout_confirm", "thumb_path"]
        vals = [c.get(k) for k in cols]
        vals[cols.index("review_status")] = "pending"
        vals[cols.index("hook_text")] = e["hook"].get("text")
        cid = db.ex(f"""INSERT INTO clips({', '.join(cols)}, title, variant_of, variant_label, duration_sec)
                        VALUES ({', '.join('?' * len(cols))}, ?, ?, ?, ?)""",
                    (*vals, title, root, label, round(T.duration(e), 2)))
        project.create(cid, e, f"Variant {label}: {what}")
        made.append({"clip_id": cid, "label": label, "what": what, "title": title})
    return made
