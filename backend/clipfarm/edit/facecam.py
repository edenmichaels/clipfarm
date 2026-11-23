"""Facecam handling (v0.8.5): the exact webcam crop, the corner picture-in-picture and "focus on the streamer".

Layouts that use the facecam (all with ONE fixed crop per shot, switches are instant hard cuts):
  corner   gameplay fills the 9:16 frame, the facecam sits in a top corner (30-38 % of the width) with a thin
           white border, rounded corners and a soft shadow                              <- default with a facecam
  focus    the streamer fills the frame, the gameplay sits small in that same corner   <- when they ARE the moment
  split    facecam on top, gameplay below (kept as an option)

Which box is the facecam: the box you drew in the facecam editor for that streamer wins; otherwise the detector's
exact box when it's confident; otherwise its best guess, and the Editor asks you once to draw it.
"""
from __future__ import annotations

import re

import numpy as np

from .. import db

W, H = 1080, 1920
PIP_W = 0.34                    # default width as a fraction of the frame (30-38 %)
PIP_RANGE = (0.25, 0.45)        # what you can resize it to
MARGIN = 44                     # from the frame edge
TOP = 220                       # below the apps' top bar
TOP_UNDER_TITLE = 440           # below a title (the title sits at the top, max 2 lines)
CORNERS = ("tl", "tr", "bl", "br")
CONFIDENT = 0.6
BORDER = 5
RADIUS = 22


# ---- which box -------------------------------------------------------------------------------------------
def saved_box(streamer_id: int | None) -> dict | None:
    return db.get_setting(f"facecam:{streamer_id}") if streamer_id else None


def resolve(detected: dict | None, confidence: float, saved: dict | None) -> tuple[dict | None, str]:
    """-> (box, source): source is "saved", "detected", "guess" (ask once) or "none"."""
    if saved:
        return saved, "saved"
    if detected and confidence >= CONFIDENT:
        return detected, "detected"
    if detected:
        return detected, "guess"
    return None, "none"


def should_ask(streamer_id: int | None, source: str) -> bool:
    """Ask once per streamer to draw the box, only when we had to guess."""
    if source != "guess" or not streamer_id:
        return False
    asked = db.get_setting("facecam_asked", {}) or {}
    if asked.get(str(streamer_id)):
        return False
    asked[str(streamer_id)] = True
    db.set_setting("facecam_asked", asked)
    return True


# ---- the corner picture-in-picture --------------------------------------------------------------------------
def cam_aspect(cam: dict | None, sw: int, sh: int) -> float:
    if not cam:
        return 16 / 9
    return float(np.clip(cam["w"] * sw / max(1e-6, cam["h"] * sh), 0.9, 2.2))


def pip_rect(pip: dict | None, aspect: float, has_title: bool = False) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the picture-in-picture on the 1080x1920 frame (border included)."""
    pip = pip or {}
    wf = float(np.clip(float(pip.get("w", PIP_W)), *PIP_RANGE))
    pw = int(round(W * wf / 2)) * 2
    ph = int(round(pw / aspect / 2)) * 2
    corner = pip.get("corner") if pip.get("corner") in CORNERS else "tr"
    x = MARGIN if corner in ("tl", "bl") else W - MARGIN - pw
    if corner in ("tl", "tr"):
        y = TOP_UNDER_TITLE if has_title else TOP
    else:                       # bottom corners sit above the caption area
        from ..editing.captions import CAP_TOP_LIMIT
        y = CAP_TOP_LIMIT - 30 - ph
    return x, y, pw, ph


def importance(frames: list, crop_x: float, crop_w: float, cam: dict | None, sw: int) -> np.ndarray:
    """How much the viewer would lose if something covered each part of the 9:16 gameplay picture
    (96x54 grid over 1920x1080 portrait): the game's HUD (sharp, still, high-contrast: minimap, wanted
    level, speedo) counts most, then action (motion)."""
    import cv2
    if not frames:
        return np.zeros((96, 54), np.float32)
    h, w = frames[0][1].shape[:2]
    x0 = int(crop_x * w / sw)
    cw = max(2, int(crop_w * w / sw))
    gs = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)[:, x0:x0 + cw].astype(np.float32) for _, f in frames]
    gs = [cv2.resize(g, (54, 96), interpolation=cv2.INTER_AREA) for g in gs]
    st = np.stack(gs)
    edges = np.mean([np.abs(cv2.Laplacian(g, cv2.CV_32F)) for g in st], axis=0)
    still = 1.0 / (1.0 + st.std(axis=0) / 6.0)
    motion = np.abs(np.diff(st, axis=0)).mean(axis=0) if len(st) > 1 else np.zeros_like(edges)
    hud = edges * still
    imp = hud / (hud.max() + 1e-3) * 1.0 + motion / (motion.max() + 1e-3) * 0.4
    return imp


def choose_corner(imp: np.ndarray, aspect: float, has_title: bool, pip: dict | None = None) -> str:
    """The top corner that covers the least important detail (kept for the whole clip)."""
    best, cost = "tr", None
    for c in ("tr", "tl"):
        x, y, pw, ph = pip_rect({**(pip or {}), "corner": c}, aspect, has_title)
        gx0, gx1 = int(x / W * 54), int((x + pw) / W * 54) + 1
        gy0, gy1 = int(y / H * 96), int((y + ph) / H * 96) + 1
        v = float(imp[gy0:gy1, gx0:gx1].mean()) if imp.size else 0.0
        if cost is None or v < cost - 1e-6:
            best, cost = c, v
    return best


# ---- focus on the streamer ----------------------------------------------------------------------------------
REACT = re.compile(r"^(ha(ha)+|lol|lmao+|bro+|no+|what+|yo+|dude|omg|wtf|oh+|wait|nah+|stop|why|bruh|yes+)$", re.I)
SWITCH_GAP = 3.0                # never more than one switch every ~3 s


def _starts(words: list[dict]) -> list[float]:
    """Natural switch points: the start of a sentence or of a reaction (after a pause or punctuation)."""
    out, prev = [], None
    for w in words:
        s = float(w["start"])
        if prev is None or s - float(prev["end"]) >= 0.25 or re.search(r"[.!?,]$", prev["word"]) \
                or REACT.match(re.sub(r"[^\w']", "", w["word"])):
            out.append(s)
        prev = w
    return out


def _inside_word(t: float, words: list[dict]) -> dict | None:
    return next((w for w in words if float(w["start"]) < t < float(w["end"])), None)


def plan_focus(a: float, b: float, words: list[dict], motion: list[tuple[float, float]]) -> list[tuple[float, str]]:
    """[(source time, "corner"|"focus")] -- hard cuts between the gameplay-with-corner-cam and the streamer.

    Focus when the streamer IS the moment: talking (voice activity) or reacting (laughs, shouts) while the
    gameplay is calm. motion: [(t, gameplay motion)] from the frames."""
    ws = sorted([w for w in words or [] if float(w["end"]) > a and float(w["start"]) < b], key=lambda w: w["start"])
    if not ws or b - a < 4:
        return [(a, "corner")]
    step = 0.5
    ts = np.arange(a, b, step)
    speech = np.array([sum(max(0.0, min(float(w["end"]), t + 1) - max(float(w["start"]), t)) for w in ws) for t in ts])
    react = np.array([sum(1.0 for w in ws if t <= float(w["start"]) < t + 1
                          and (REACT.match(re.sub(r"[^\w']", "", w["word"])) or w["word"].endswith("!")
                               or (w["word"].isupper() and len(w["word"]) > 2))) for t in ts])
    if motion:
        mt, mv = np.array([m[0] for m in motion]), np.array([m[1] for m in motion])
        mo = np.interp(ts, mt, mv)
        ref = float(np.percentile(mv, 75)) + 1e-6
        mo = np.clip(mo / ref, 0, 2)
    else:
        mo = np.zeros_like(ts)
    k = np.ones(5) / 5
    sm = lambda x: np.convolve(np.pad(x, 2, mode="edge"), k, "valid")  # noqa: E731
    # talking most of the time (>= 60 % of each second) counts fully; reactions add; busy gameplay takes away
    score = 0.55 * np.clip(sm(speech) / 0.6, 0, 1) + 0.5 * np.clip(sm(react), 0, 1.5) - 0.7 * sm(mo)
    focus = score > 0.35
    # runs -> segments of at least SWITCH_GAP seconds
    segs: list[list] = []
    for t, f in zip(ts, focus):
        lay = "focus" if f else "corner"
        if segs and segs[-1][1] == lay:
            continue
        segs.append([float(t), lay])
    segs[0][0] = a
    # stretches shorter than SWITCH_GAP are absorbed by the longer neighbour (no flicker between layouts)
    while len(segs) > 1:
        lens = [(segs[i + 1][0] if i + 1 < len(segs) else b) - segs[i][0] for i in range(len(segs))]
        i = min(range(len(segs)), key=lambda j: lens[j])
        if lens[i] >= SWITCH_GAP:
            break
        if i == 0:
            segs[1][0] = a
            segs.pop(0)
        elif i == len(segs) - 1 or lens[i - 1] >= lens[i + 1]:
            segs.pop(i)                                  # the previous layout simply continues
        else:
            segs[i + 1][0] = segs[i][0]
            segs.pop(i)
        merged_ = [segs[0]]
        for sg in segs[1:]:
            if sg[1] != merged_[-1][1]:
                merged_.append(sg)
        segs = merged_
    merged = segs
    # snap switches to natural points, never mid-word, and keep them >= SWITCH_GAP apart
    points = _starts(ws)
    out: list[tuple[float, str]] = [(a, merged[0][1])]   # the clip's start isn't a switch
    for t, lay in merged[1:]:
        cand = [p for p in points if abs(p - t) <= 1.5]
        t2 = min(cand, key=lambda p: abs(p - t)) if cand else t
        if (w := _inside_word(t2, ws)) is not None:
            t2 = float(w["start"])
        first = len(out) == 1
        if first and t2 - a < 1.5:
            out[0] = (a, lay)                          # starts with it: no switch in the first moment
            continue
        if (not first and t2 - out[-1][0] < SWITCH_GAP) or b - t2 < SWITCH_GAP * 0.5:
            continue
        out.append((round(t2, 3), lay))
    return out
