"""9:16 reframing.

Layouts (all output 1080x1920):
  split      facecam on top (1/3), gameplay on the bottom (2/3)
  crop       9:16 crop that pans to follow on-screen motion (action tracking)
  blur_fill  full 16:9 frame centred over a blurred, zoomed copy of itself
  auto       split if a facecam is found, else crop

Facecam boxes can be saved per streamer (most streamers keep their cam in one place),
so detection only runs once.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

W, H = 1080, 1920


@dataclass
class Box:
    """Fractions of source width/height (0..1)."""
    x: float
    y: float
    w: float
    h: float


def _sample_frames(path: Path, start: float, end: float, n: int, width: int = 480):
    """n frames spread over [start, end], decoded exactly by FFmpeg (OpenCV seeks snap to keyframes)."""
    from ..edit.reframe import sample
    fps = max(0.2, n / max(0.5, end - start))
    return sample(path, start, end, fps, width)[:n]


def _sample_frames_cv(path: Path, start: float, end: float, n: int, width: int = 480):
    import cv2  # opencv-python-headless
    cap = cv2.VideoCapture(str(path))
    frames = []
    for t in np.linspace(start, max(start, end - 0.1), n):
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        frames.append((float(t), cv2.resize(frame, (width, int(h * width / w)))))
    cap.release()
    return frames


def detect_facecam(path: Path, start: float, end: float) -> Box | None:
    """Find a stable face in the frame corners/edges -> facecam box. None if no consistent face."""
    import cv2
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    hits = []
    frames = _sample_frames(path, start, end, 12)
    for _, f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        fh, fw = gray.shape
        faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(int(fw * 0.04), int(fw * 0.04)))
        for (x, y, w, h) in faces:
            cx, cy = (x + w / 2) / fw, (y + h / 2) / fh
            # Facecams sit near edges; a face in the dead centre is likely gameplay (cutscene).
            if 0.3 < cx < 0.7 and 0.3 < cy < 0.7:
                continue
            hits.append((cx, cy, w / fw, h / fh))
    if len(hits) < max(3, len(frames) // 3):
        return None
    cx, cy, fw_, fh_ = np.median(np.array(hits), axis=0)
    # Webcam frame is usually ~3.5x the face width; keep a 16:9-ish box.
    bw = min(0.45, fw_ * 3.5)
    bh = min(0.5, bw)  # on a 16:9 source, equal fractions give a 16:9 box
    bx = float(np.clip(cx - bw / 2, 0, 1 - bw))
    by = float(np.clip(cy - bh / 2, 0, 1 - bh))
    return Box(bx, by, float(bw), float(bh))


def track_action(path: Path, start: float, end: float, samples: int = 24) -> list[tuple[float, float]]:
    """Return [(t_rel, centre_x_fraction)] following motion; smoothed to avoid jitter."""
    import cv2
    frames = _sample_frames(path, start, end, samples)
    pts: list[tuple[float, float]] = []
    prev = None
    for t, f in frames:
        g = cv2.GaussianBlur(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (9, 9), 0)
        if prev is not None:
            diff = cv2.absdiff(g, prev).astype(np.float32)
            col = diff.sum(axis=0)
            cx = float((col * np.arange(col.size)).sum() / col.sum()) / col.size if col.sum() > 1e3 else 0.5
            pts.append((t - start, cx))
        prev = g
    if not pts:
        return [(0.0, 0.5)]
    xs = np.array([p[1] for p in pts])
    k = 5
    xs = np.convolve(np.pad(xs, (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid")
    xs = 0.5 + (xs - 0.5) * 0.8   # damp extremes; centre-weighted crops look calmer
    return [(pts[i][0], float(xs[i])) for i in range(len(pts))]


def _lerp_expr(keys: list[tuple[float, float]], src_w: int, crop_w: int) -> str:
    """Piecewise-linear FFmpeg expression for crop x over time."""
    def px(cx: float) -> int:
        return int(np.clip(cx * src_w - crop_w / 2, 0, src_w - crop_w))
    if len(keys) == 1:
        return str(px(keys[0][1]))
    expr = str(px(keys[-1][1]))
    for (t0, c0), (t1, c1) in reversed(list(zip(keys, keys[1:]))):
        x0, x1 = px(c0), px(c1)
        seg = f"{x0}+({x1}-{x0})*(t-{t0:.2f})/{max(t1 - t0, 0.01):.2f}"
        expr = f"if(lt(t,{t1:.2f}),{seg},{expr})"
    return f"if(lt(t,{keys[0][0]:.2f}),{px(keys[0][1])},{expr})"


def filtergraph(layout: str, src_w: int, src_h: int, facecam: Box | None = None,
                track: list[tuple[float, float]] | None = None) -> tuple[str, str]:
    """Returns (filter_complex_string, output_label). Input is [0:v]."""
    if layout == "split" and facecam:
        cam_h, game_h = 640, H - 640
        fx, fy = int(facecam.x * src_w), int(facecam.y * src_h)
        fw, fh = int(facecam.w * src_w), int(facecam.h * src_h)
        game_crop_w = min(src_w, int(src_h * W / game_h))
        return (f"[0:v]split=2[a][b];"
                f"[a]crop={fw}:{fh}:{fx}:{fy},scale={W}:{cam_h}:force_original_aspect_ratio=increase,"
                f"crop={W}:{cam_h}[cam];"
                f"[b]crop={game_crop_w}:{src_h}:(iw-{game_crop_w})/2:0,scale={W}:{game_h}:force_original_aspect_ratio=increase,"
                f"crop={W}:{game_h}[game];"
                f"[cam][game]vstack=inputs=2,setsar=1[v0]", "v0")
    if layout == "crop":
        crop_w = min(src_w, int(src_h * 9 / 16)) // 2 * 2
        x = _lerp_expr(track or [(0.0, 0.5)], src_w, crop_w)
        return f"[0:v]crop={crop_w}:{src_h}:'{x}':0,scale={W}:{H},setsar=1[v0]", "v0"
    # blur_fill (also the safe fallback)
    return (f"[0:v]split=2[bg][fg];"
            f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2,eq=brightness=-0.08[bgb];"
            f"[fg]scale={W}:-2[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]", "v0")
