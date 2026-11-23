"""AI reframe to 9:16: scene type per shot -> layout keyframes, ONE fixed crop per shot (never a camera move).

Scene types           Default layout
  facecam+gameplay      corner (gameplay fills the frame, facecam in a top corner); "focus" (the streamer fills
                        the frame) when they're the moment
  gameplay              crop   (fixed crop centred on the action: motion + saliency)
  people / IRL          speaker (fixed crop on the main speaker)
  screen / menu         blur_fill (whole frame, nothing lost)
Also available: split (facecam top, gameplay bottom), pip (older small corner cam).

Rules: a campaign that needs its overlay visible ("streamer_overlay") gets the whole frame (blur_fill)
in every shot; one that needs the streamer's face uses split / pip whenever a facecam exists.
Nothing ever pans: each shot is one fixed crop, and a layout change is an instant hard cut.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from ..editing.layout import Box
from .. import proc

LAYOUTS = ["corner", "focus", "split", "crop", "speaker", "pip", "blur_fill"]
SAMPLE_FPS = 2.0


def sample(path: Path, a: float, b: float, fps: float = SAMPLE_FPS, width: int = 320):
    """Frames at exact times via FFmpeg (OpenCV seeking snaps to keyframes and repeats frames)."""
    from ..editing.ffmpeg_util import ffmpeg_bin, probe
    info = probe(path)
    h = max(2, int(round(info["height"] * width / max(1, info["width"]) / 2)) * 2)
    cmd = [ffmpeg_bin(), "-v", "error", "-ss", f"{a:.3f}", "-t", f"{max(0.05, b - a):.3f}", "-i", str(path), "-an",
           "-vf", f"fps={fps},scale={width}:{h}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    raw = proc.run(cmd, capture_output=True).stdout
    n = len(raw) // (width * h * 3)
    frames = np.frombuffer(raw[: n * width * h * 3], np.uint8).reshape(n, h, width, 3)
    return [(round(a + i / fps, 3), frames[i].copy()) for i in range(n)]


def _faces_haar(frame) -> list[tuple[float, float, float, float]]:
    """Faces as (cx, cy, w, h) fractions. No face model available -> no faces (layouts still work)."""
    import cv2
    if not hasattr(_faces_haar, "c"):
        try:
            c = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            _faces_haar.c = None if c.empty() else c
        except Exception:
            _faces_haar.c = None
    if _faces_haar.c is None:
        return []
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    fh, fw = g.shape
    res = _faces_haar.c.detectMultiScale(g, 1.1, 5, minSize=(max(12, int(fw * 0.04)),) * 2)
    return [((x + w / 2) / fw, (y + h / 2) / fh, w / fw, h / fh) for (x, y, w, h) in res]


def shot_bounds(frames: list) -> list[int]:
    """Indices where a new shot starts (hard cuts)."""
    import cv2
    starts, prev = [0], None
    for i, (_, f) in enumerate(frames):
        hist = cv2.calcHist([f], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3).flatten()
        hist /= hist.sum() + 1e-6
        if prev is not None and 0.5 * np.abs(hist - prev).sum() > 0.45 and i - starts[-1] >= 2:
            starts.append(i)
        prev = hist
    return starts


def action_path(frames: list) -> list[tuple[float, float]]:
    """Where the action is, per sample: motion energy weighted by saliency (bright/contrasty areas)."""
    import cv2
    pts, prev = [], None
    for t, f in frames:
        g = cv2.GaussianBlur(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (7, 7), 0).astype(np.float32)
        if prev is not None:
            motion = np.abs(g - prev)
            sal = np.abs(g - cv2.GaussianBlur(g, (0, 0), 9))          # cheap spectral-free saliency
            w = motion * (1 + sal / (sal.mean() + 1e-3))
            col = w.sum(axis=0)
            if col.sum() > 50:
                pts.append((t, float((col * np.arange(col.size)).sum() / col.sum() / col.size)))
            else:
                pts.append((t, pts[-1][1] if pts else 0.5))
        prev = g
    if pts:
        pts.insert(0, (frames[0][0], pts[0][1]))
    return pts


def speaker_path(frames: list, speech: list[tuple[float, float]],
                 faces_fn: Callable = _faces_haar) -> list[tuple[float, float]]:
    """Follow the active speaker: among the faces, the one whose mouth moves while someone is talking."""
    pts, prev_gray, cur = [], None, None
    for t, f in frames:
        import cv2
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        faces = faces_fn(f)
        talking = any(a - 0.2 <= t <= b + 0.2 for a, b in speech)
        if faces:
            if prev_gray is not None and len(faces) > 1 and talking:
                H, W = g.shape

                def mouth_motion(fc):
                    cx, cy, fw, fh = fc
                    x0, x1 = int((cx - fw / 3) * W), int((cx + fw / 3) * W)
                    y0, y1 = int((cy + fh * 0.1) * H), int((cy + fh / 2) * H)
                    return float(np.abs(g[y0:y1, x0:x1] - prev_gray[y0:y1, x0:x1]).mean()) if x1 > x0 and y1 > y0 else 0
                best = max(faces, key=mouth_motion)
            elif cur is not None:
                best = min(faces, key=lambda fc: abs(fc[0] - cur))       # stick with who we were following
            else:
                best = max(faces, key=lambda fc: fc[2])                  # biggest face first
            cur = best[0]
        pts.append((t, cur if cur is not None else 0.5))
        prev_gray = g
    return pts


def classify(frames: list, facecam: Box | None, faces_fn: Callable = _faces_haar) -> str:
    import cv2
    if not frames:
        return "gameplay"
    big_central, motion, edges, prev = 0, [], [], None
    for _, f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        for cx, cy, fw, fh in faces_fn(f):
            inside_cam = facecam and facecam.x <= cx <= facecam.x + facecam.w and facecam.y <= cy <= facecam.y + facecam.h
            if fw > 0.1 and not inside_cam:
                big_central += 1
                break
        if prev is not None:
            motion.append(float(np.abs(g.astype(np.float32) - prev).mean()) / 255)
        edges.append(float((cv2.Canny(g, 60, 160) > 0).mean()))
        prev = g.astype(np.float32)
    if big_central >= max(2, 0.4 * len(frames)):
        return "people"
    if (np.mean(motion) if motion else 0) < 0.006 and np.mean(edges) > 0.05:
        return "screen"
    return "facecam+gameplay" if facecam else "gameplay"


DEFAULT_FOR = {"facecam+gameplay": "corner", "gameplay": "crop", "people": "speaker", "screen": "blur_fill"}


def _mask_cam(frames: list, cam: Box | None) -> list:
    """Frames with the webcam blacked out, so the streamer's movement never pulls the gameplay crop."""
    if not cam:
        return frames
    out = []
    for t, f in frames:
        g = f.copy()
        h, w = g.shape[:2]
        g[int(cam.y * h):int((cam.y + cam.h) * h) + 1, int(cam.x * w):int((cam.x + cam.w) * w) + 1] = 0
        out.append((t, g))
    return out


def gameplay_motion(frames: list) -> list[tuple[float, float]]:
    import cv2
    out, prev = [], None
    for t, f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            out.append((t, float(np.abs(g - prev).mean())))
        prev = g
    return out


def analyse(path: Path, a: float, b: float, *, facecam: Box | None = None, onscreen: list[str] | None = None,
            speech: list[tuple[float, float]] | None = None, detect_cam: bool = True,
            faces_fn: Callable = _faces_haar, words: list[dict] | None = None, has_title: bool = False,
            pip: dict | None = None, focus: bool = True) -> dict:
    """-> {"facecam", "facecam_source", "facecam_confidence", "pip", "keyframes": [{t, layout, scene, cam, path}]}
    (source time). `facecam` = the box you saved for this streamer (wins over detection).

    Every shot gets ONE fixed crop; with a facecam the default is gameplay + facecam in a corner, switching
    (hard cut) to the streamer when they're the moment."""
    from . import facecam as F
    onscreen = onscreen or []
    det, conf = None, 0.0
    if facecam is None and detect_cam:
        try:
            from ..editing.layout import detect_facecam_exact
            det, conf = detect_facecam_exact(path, a, b)
        except Exception:
            det, conf = None, 0.0
    box, source = F.resolve(det.__dict__ if det else None, conf, facecam.__dict__ if facecam else None)
    cam = Box(**box) if box else None
    frames = sample(path, a, b)
    result = {"facecam": box, "facecam_source": source, "facecam_confidence": round(conf, 2), "pip": None}
    if not frames:
        return {**result, "keyframes": [{"t": a, "layout": "blur_fill", "scene": "unknown", "cam": None,
                                         "path": [[0, 0.5]]}]}
    masked = _mask_cam(frames, cam)
    starts = shot_bounds(frames) + [len(frames)]
    kfs = []
    for s_, e_ in zip(starts, starts[1:]):
        sh = frames[s_:e_]
        scene = classify(sh, cam, faces_fn)
        lay = DEFAULT_FOR[scene]
        if "streamer_overlay" in onscreen:
            lay = "blur_fill"                                  # never crop the required overlay
        elif "streamer_face" in onscreen and lay in ("crop", "speaker"):
            lay = "corner" if cam else "blur_fill"
        if lay in ("corner", "split") and not cam:
            lay = "crop"
        t0 = a if not kfs else sh[0][0]
        if lay == "speaker":
            raw = speaker_path(sh, speech or [], faces_fn)
        elif lay in ("crop", "pip", "corner", "split"):
            raw = action_path(masked[s_:e_]) or [(sh[0][0], 0.5)]
        else:
            raw = [(sh[0][0], 0.5)]
        # ONE fixed crop per shot (no panning, drifting or tracking): the shot's average framing
        xs = [x for _, x in raw] or [0.5]
        kf = {"t": round(t0, 3), "layout": lay, "scene": scene, "cam": box, "path": [[0.0, round(sum(xs) / len(xs), 4)]]}
        if kfs and kfs[-1]["layout"] == lay and lay in ("split", "blur_fill"):
            continue                                           # same static layout: no new keyframe
        kfs.append(kf)
    # the corner: fixed for the whole clip, on the side that hides the least (HUD first, then action)
    if cam and any(k["layout"] == "corner" for k in kfs):
        if pip and pip.get("corner"):
            result["pip"] = {"corner": pip["corner"], "w": float(pip.get("w", F.PIP_W)), "source": "yours"}
        else:
            k0 = next(k for k in kfs if k["layout"] == "corner")
            sw, sh_ = frames[0][1].shape[1], frames[0][1].shape[0]
            cw = sh_ * 9 / 16
            cx = min(max(0.0, k0["path"][0][1] * sw - cw / 2), sw - cw)
            imp = F.importance(masked, cx, cw, box, sw)
            asp = F.cam_aspect(box, sw, sh_)
            wf = float((pip or {}).get("w", F.PIP_W))
            result["pip"] = {"corner": F.choose_corner(imp, asp, has_title, {"w": wf}), "w": wf, "source": "auto"}
        # focus on the streamer when they ARE the moment (hard cuts at sentence starts, >= 3 s apart)
        if focus and "streamer_overlay" not in onscreen:
            plan = F.plan_focus(a, b, words or [], gameplay_motion(masked))
            kfs = _apply_focus(kfs, plan)
    result["keyframes"] = kfs
    return result


def _apply_focus(kfs: list[dict], plan: list[tuple[float, str]]) -> list[dict]:
    """Overlay the focus plan on the shot keyframes: inside a focus stretch, corner shots become focus."""
    if all(lay == "corner" for _, lay in plan):
        return kfs
    pts = sorted({k["t"] for k in kfs} | {t for t, _ in plan})
    out = []
    for t in pts:
        shot = next(k for k in reversed(kfs) if k["t"] <= t + 1e-6) if any(k["t"] <= t + 1e-6 for k in kfs) else kfs[0]
        want = next(lay for pt, lay in reversed(plan) if pt <= t + 1e-6) if any(pt <= t + 1e-6 for pt, _ in plan) else "corner"
        lay = "focus" if want == "focus" and shot["layout"] == "corner" else shot["layout"]
        k = {**shot, "t": round(t, 3), "layout": lay}
        if out and out[-1]["layout"] == lay and out[-1]["path"] == k["path"]:
            continue
        out.append(k)
    return out


def enforce_rules(keyframes: list[dict], onscreen: list[str], facecam: dict | None) -> tuple[list[dict], list[str]]:
    """Fix keyframes that break campaign rules. Returns (keyframes, what changed)."""
    out, fixed = [], []
    for k in keyframes:
        k = dict(k)
        if "streamer_overlay" in onscreen and k["layout"] != "blur_fill":
            fixed.append(f"{k['layout']} at {k['t']:.1f}s would crop the required overlay -> full frame")
            k["layout"], k["path"] = "blur_fill", [[0, 0.5]]
        elif "streamer_face" in onscreen and (k["layout"] in ("crop", "speaker") or
                                              (k["layout"] in ("pip", "corner", "focus") and not facecam)):
            to = "corner" if facecam else "blur_fill"
            fixed.append(f"{k['layout']} at {k['t']:.1f}s could hide the streamer -> "
                         f"{'facecam in the corner' if facecam else 'full frame'}")
            k["layout"] = to
            if to == "corner":
                k["cam"] = facecam
            else:
                k["path"] = [[0, 0.5]]
        out.append(k)
    return out, fixed
