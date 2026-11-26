"""9:16 reframing.

Layouts (all output 1080x1920):
  split      facecam on top, gameplay on the bottom
  crop       one fixed 9:16 crop per shot, centred on the action (never pans)
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


def _gray_stack(frames) -> np.ndarray:
    import cv2
    return np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for _, f in frames])


def refine_facecam(stack: np.ndarray, rough: Box) -> tuple[Box, float]:
    """Snap a rough cam box (from the face) to the webcam frame's real edges, then step inside any border.

    Overlay borders and webcam edges are straight lines that stay put while the picture around and inside them
    changes, so: average the edge strength over time, look for the strongest line near each side of the rough box,
    then skip inwards past static, uniform rows/columns (the border itself, alert frames). Returns the exact box
    and a confidence 0..1 (how clearly all four sides stand out)."""
    import cv2
    n, h, w = stack.shape
    gx = np.mean([np.abs(cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=3)) for f in stack], axis=0)
    gy = np.mean([np.abs(cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=3)) for f in stack], axis=0)
    tstd = stack.std(axis=0)
    x0, y0, x1, y1 = rough.x * w, rough.y * h, (rough.x + rough.w) * w, (rough.y + rough.h) * h
    rw, rh = x1 - x0, y1 - y0

    def best_line(prof: np.ndarray, lo: float, hi: float) -> tuple[int, float]:
        lo, hi = int(max(1, lo)), int(min(len(prof) - 2, hi))
        if hi <= lo:
            return int(np.clip((lo + hi) // 2, 0, len(prof) - 1)), 0.0
        seg = prof[lo:hi + 1]
        i = int(np.argmax(seg))
        base = float(np.median(prof[max(0, lo - 20):hi + 21])) + 1e-3
        return lo + i, float(seg[i]) / base

    ys, ye = int(max(0, y0 + rh * 0.2)), int(min(h, y1 - rh * 0.2))
    xs, xe = int(max(0, x0 + rw * 0.2)), int(min(w, x1 - rw * 0.2))
    colp = gx[ys:ye].mean(axis=0) if ye > ys else gx.mean(axis=0)     # vertical lines (left / right sides)
    rowp = gy[:, xs:xe].mean(axis=1) if xe > xs else gy.mean(axis=1)  # horizontal lines (top / bottom)
    L, rl = best_line(colp, x0 - rw * 0.35, x0 + rw * 0.25)
    R, rr = best_line(colp, x1 - rw * 0.25, x1 + rw * 0.35)
    T, rt = best_line(rowp, y0 - rh * 0.35, y0 + rh * 0.25)
    B, rb = best_line(rowp, y1 - rh * 0.25, y1 + rh * 0.35)
    # a cam in the very corner has no line on that side: the frame edge is the side
    if x0 <= 2 and rl < 1.6:
        L, rl = 0, 3.0
    if y0 <= 2 and rt < 1.6:
        T, rt = 0, 3.0
    if x1 >= w - 2 and rr < 1.6:
        R, rr = w - 1, 3.0
    if y1 >= h - 2 and rb < 1.6:
        B, rb = h - 1, 3.0
    conf = float(np.clip((min(rl, rr, rt, rb) - 1.3) / 2.2, 0, 1))
    if R - L < rw * 0.4 or B - T < rh * 0.4:          # nonsense: keep the rough box
        return rough, 0.0

    def static_uniform_col(x: int) -> bool:
        c = stack[:, T:B, x]
        return float(tstd[T:B, x].mean()) < 4.0 and float(c.mean(axis=0).std()) < 14.0

    def static_uniform_row(y: int) -> bool:
        r = stack[:, y, L:R]
        return float(tstd[y, L:R].mean()) < 4.0 and float(r.mean(axis=0).std()) < 14.0
    cap_x, cap_y = max(3, int((R - L) * 0.06)), max(3, int((B - T) * 0.06))
    # step inside: past the edge pixel itself, then past any static, uniform border / overlay frame
    L0, R0, T0, B0 = L, R, T, B
    L, R, T, B = L + 1, R - 1, T + 1, B - 1
    while L - L0 < cap_x and L < R and static_uniform_col(L):
        L += 1
    while R0 - R < cap_x and R > L and static_uniform_col(R):
        R -= 1
    while T - T0 < cap_y and T < B and static_uniform_row(T):
        T += 1
    while B0 - B < cap_y and B > T and static_uniform_row(B):
        B -= 1
    return Box(L / w, T / h, (R - L + 1) / w, (B - T + 1) / h), conf


def detect_facecam_exact(path: Path, start: float, end: float) -> tuple[Box | None, float]:
    """(exact webcam box, confidence 0..1). None if there's no stable face near the frame edges."""
    rough = detect_facecam(path, start, end)
    if rough is None:
        return None, 0.0
    frames = _sample_frames(path, start, end, 16, 640)
    if len(frames) < 3:
        return rough, 0.0
    return refine_facecam(_gray_stack(frames), rough)


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


def fixed_x(keys: list[tuple[float, float]], src_w: int, crop_w: int) -> int:
    """The ONE crop x (pixels) for a shot: the average framing of its keys. ClipFarm never pans (v0.8.5)."""
    xs = [float(x) for _, x in (keys or [(0.0, 0.5)])] or [0.5]
    cx = sum(xs) / len(xs)
    return int(np.clip(cx * src_w - crop_w / 2, 0, src_w - crop_w)) // 2 * 2


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
        x = fixed_x(track or [(0.0, 0.5)], src_w, crop_w)
        return f"[0:v]crop={crop_w}:{src_h}:{x}:0,scale={W}:{H},setsar=1[v0]", "v0"
    # blur_fill (also the safe fallback)
    return (f"[0:v]split=2[bg][fg];"
            f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2,eq=brightness=-0.08[bgb];"
            f"[fg]scale={W}:-2[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]", "v0")
