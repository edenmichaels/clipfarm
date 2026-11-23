"""Automatic zooms, punch-ins, flashes, shake, reaction emojis, sound effects and B-roll for an edit project.

Placed from the moment type and the payoff, snapped to the music's beats when music is on. Everything added
here is marked "auto": running it again replaces the automatic ones and leaves anything you added by hand.
Sound effects come only from the bundled CC0 pack or your own SFX folder; B-roll only from your own folder.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from .. import db
from . import timeline as T

REACTION = re.compile(r"^(no+|what+|bro+|yo+|oh+|omg|wow+|wait|dude|holy|yes+|let'?s|go+|nah+|why|how)$", re.I)
PLAN = {   # moment type: (zoom amount at payoff, flash, shake, emoji, sfx at payoff, sfx before payoff)
    "Funny": (1.16, False, False, "😂", "pop", None),
    "Action/Explosion": (1.24, True, True, "💥", "boom", "whoosh"),
    "Rage/Reaction": (1.2, False, True, "😱", "impact", None),
    "Clutch/Skill": (1.18, True, False, "🏆", "ding", "riser"),
    "Fail": (1.12, False, False, "💀", "bonk", None),
    "Chaos/Fail": (1.14, False, True, "💀", "bonk", None),
    "Action + Reaction": (1.24, True, True, "💥", "boom", "whoosh"),
    "Rage": (1.2, False, True, "😱", "impact", None),
    "Wholesome": (1.08, False, False, "❤️", "ding", None),
}


def _snap(t_src: float, edl: dict, beats_out: list[float], window: float = 0.3) -> float:
    """Move a source time onto the nearest music beat (if one is within `window` seconds)."""
    o = T.to_out(edl, t_src)
    if o is None or not beats_out:
        return t_src
    b = min(beats_out, key=lambda x: abs(x - o))
    return T.to_src(edl, b) if abs(b - o) <= window else t_src


def auto_effects(edl: dict, *, emoji: bool = True, sfx: bool = True, broll: bool = True,
                 max_extra_zooms: int = 2) -> dict:
    e = copy.deepcopy(edl)
    for k in ("zooms", "overlays", "sfx", "broll"):
        e[k] = [x for x in e.get(k, []) if not x.get("auto")]
    peak = e["moment"].get("peak")
    a, b = e["range"]
    m = e["music"]
    music_on = bool(m.get("enabled") and m.get("path") and not e["rules"].get("no_music"))
    beats = [float(x) for x in (m.get("zoom_beats") or m.get("beats") or [])] if music_on else []
    zoom, flash, shake, emo, fx_hit, fx_pre = PLAN.get(e["moment"].get("type") or "", (1.12, False, False, None, None, None))
    pack = {}
    if sfx:
        from ..music import sfx as sfxlib
        pack = {s["id"]: s for s in sfxlib.listing()}

    def add_sfx(sid: str | None, t: float, gain: float = -8.0):
        if sid and sid in pack and a <= t <= b:
            s = pack[sid]
            e["sfx"].append({"id": sid, "path": s["path"], "t": round(t, 3), "gain_db": gain, "licence": s["licence"],
                             "auto": True})

    if peak is not None and a <= peak <= b:
        tp = _snap(peak, e, beats)
        e["zooms"].append({"t": round(tp, 3), "kind": "punch", "amount": zoom, "dur": 0.6, "auto": True})
        if flash:
            e["overlays"].append({"kind": "flash", "t": round(tp, 3), "dur": 0.12, "auto": True})
        if shake:
            e["overlays"].append({"kind": "shake", "t": round(tp, 3), "dur": 0.45, "auto": True})
        if emoji and emo:
            e["overlays"].append({"kind": "emoji", "emoji": emo, "t": round(tp + 0.1, 3), "dur": 1.2, "auto": True})
        add_sfx(fx_hit, tp, -6.0 if fx_hit == "boom" else -9.0)
        if fx_pre == "riser":
            add_sfx("riser", max(a, tp - 1.6), -12.0)
        elif fx_pre == "whoosh":
            add_sfx("whoosh", max(a, tp - 0.45), -12.0)
    # small punch-ins on reaction words ("NO WAY", "bro", "what"), away from other zooms
    taken = [z["t"] for z in e["zooms"]]
    extra = 0
    for w in sorted(T.words_out(e), key=lambda x: x["start"]):
        if extra >= max_extra_zooms:
            break
        if not REACTION.match(re.sub(r"[^\w']", "", w["word"])):
            continue
        ts = T.to_src(e, w["start"])
        if any(abs(ts - x) < 2.5 for x in taken):
            continue
        ts = _snap(ts, e, beats, 0.2)
        e["zooms"].append({"t": round(ts, 3), "kind": "punch", "amount": 1.08, "dur": 0.4, "auto": True})
        taken.append(ts)
        extra += 1
    if broll:
        e["broll"] += auto_broll(e)
    e["zooms"].sort(key=lambda z: z["t"])
    return e


def broll_library() -> list[dict]:
    lib = (db.get_setting("music", {}) or {}).get("broll_dir")
    if not lib or not Path(lib).is_dir():
        return []
    out = []
    for p in sorted(Path(lib).rglob("*")):
        if p.suffix.lower() in (".mp4", ".mov", ".webm", ".png", ".jpg", ".jpeg", ".gif", ".webp"):
            words = [w for w in re.split(r"[^a-z0-9]+", p.stem.lower()) if len(w) > 2]
            out.append({"path": str(p), "name": p.stem, "keywords": words,
                        "licence": "Your own B-roll library: you confirmed you have the rights"})
    return out


def auto_broll(edl: dict, limit: int = 2) -> list[dict]:
    """A cut-away from your library when a word in the clip matches the file's name (e.g. 'cops.mp4')."""
    lib = broll_library()
    if not lib:
        return []
    out, used = [], set()
    for w in T.words_out(edl):
        word = re.sub(r"[^a-z0-9]", "", w["word"].lower())
        if len(word) < 4:
            continue
        hit = next((x for x in lib if x["path"] not in used and any(word == k or word.rstrip("s") == k.rstrip("s")
                                                                       for k in x["keywords"])), None)
        if not hit:
            continue
        ts = T.to_src(edl, w["start"])
        if any(abs(ts - o["t"]) < 4 for o in out):
            continue
        out.append({"path": hit["path"], "t": round(ts, 3), "dur": 1.6, "licence": hit["licence"], "auto": True,
                    "keyword": word})
        used.add(hit["path"])
        if len(out) >= limit:
            break
    return out
