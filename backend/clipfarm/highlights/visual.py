"""Cheap per-second visual signals: frames sampled at 4 fps at 96x54 straight from FFmpeg.

  motion     how much the picture changes (mean absolute frame difference)
  cuts       hard scene changes (histogram jump)
  flash      sudden brightness jumps (explosions, muzzle flashes)
  shake      jittery whole-frame movement (camera shake on hits / crashes)
  static     0..1, how "still" the screen is (menus, loading, maps, BRB, AFK)
  dark       0..1, mostly black screen (loading / fades)
  ui         0..1, flat colour + hard edges with no motion: looks like a menu, map or inventory
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..editing.ffmpeg_util import ffmpeg_bin

FW, FH, FPS = 96, 54, 4


def frames(media: Path):
    cmd = [ffmpeg_bin(), "-v", "error", "-i", str(media), "-an", "-vf",
           f"fps={FPS},scale={FW}:{FH}:flags=area", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = FW * FH * 3
    assert proc.stdout
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(FH, FW, 3)
    proc.wait()


def analyse(media: Path) -> dict[str, np.ndarray]:
    import cv2
    motion, cuts, flash, shake, bright, edges, colour = [], [], [], [], [], [], []
    prev = prev_hist = None
    prev_b = 0.0
    for f in frames(media):
        g = cv2.cvtColor(f, cv2.COLOR_RGB2GRAY).astype(np.float32)
        b = float(g.mean())
        hist = cv2.calcHist([f], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3).flatten()
        hist /= hist.sum() + 1e-6
        if prev is None:
            motion.append(0.0); cuts.append(0.0); flash.append(0.0); shake.append(0.0)
        else:
            motion.append(float(np.abs(g - prev).mean()) / 255)
            cuts.append(float(0.5 * np.abs(hist - prev_hist).sum()))
            flash.append(max(0.0, b - prev_b) / 255)
            (dx, dy), resp = cv2.phaseCorrelate(prev, g)
            shake.append(float(np.hypot(dx, dy)) if resp > 0.1 else 0.0)
        bright.append(b / 255)
        e = cv2.Canny(g.astype(np.uint8), 60, 160)
        edges.append(float((e > 0).mean()))
        hsv = cv2.cvtColor(f, cv2.COLOR_RGB2HSV)
        colour.append(float(hsv[..., 1].mean()) / 255)
        prev, prev_hist, prev_b = g, hist, b
    n = len(motion) // FPS
    if n == 0:
        return {}

    def sec(a, how="mean"):
        x = np.asarray(a[:n * FPS], np.float32).reshape(n, FPS)
        return x.max(axis=1) if how == "max" else x.mean(axis=1)

    m = sec(motion)
    sh = np.asarray(shake[:n * FPS], np.float32).reshape(n, FPS)
    jitter = sh.std(axis=1) + sh.mean(axis=1) * 0.3          # back-and-forth movement, not a smooth pan
    out = {"motion": m, "cuts": sec(cuts, "max"), "flash": sec(flash, "max"), "shake": jitter}
    # Stillness over a few seconds (menus, maps, loading, BRB): low motion and no cuts.
    k = 5
    mm = np.convolve(np.pad(m, (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid")
    out["static"] = np.clip(1 - mm / 0.012, 0, 1).astype(np.float32)
    out["dark"] = np.clip(1 - sec(bright) / 0.08, 0, 1).astype(np.float32)
    # Menus / maps / inventory: still, lots of hard edges (text, grids), little natural colour variation.
    ui = out["static"] * np.clip((sec(edges) - 0.06) / 0.10, 0, 1)
    out["ui"] = ui.astype(np.float32)
    return out
