"""FFmpeg discovery, encoder (GPU) detection, and filter-path escaping."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

# Installed app: the shell passes its resource folder (CLIPFARM_RESOURCE_DIR = the install folder, which
# holds bin/ffmpeg.exe and assets/fonts) and the engine exe sits in the same folder.
# Dev: backend/bin and backend/assets. Last resort: ffmpeg on PATH.
def _roots() -> list[Path]:
    roots = []
    if os.environ.get("CLIPFARM_RESOURCE_DIR"):
        roots.append(Path(os.environ["CLIPFARM_RESOURCE_DIR"]))
    roots += [Path(sys.executable).resolve().parent, Path(__file__).resolve().parents[2]]
    if getattr(sys, "_MEIPASS", None):
        roots.append(Path(sys._MEIPASS))
    return roots


_ROOTS = _roots()


def _find(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    for root in _ROOTS:
        if (root / "bin" / exe).exists():
            return str(root / "bin" / exe)
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} not found. Reinstall ClipFarm or put {name} on PATH.")
    return found


def ffmpeg_bin() -> str:
    return _find("ffmpeg")


def ffprobe_bin() -> str:
    return _find("ffprobe")


# Order matters: first one that actually encodes a test frame wins.
GPU_ENCODERS = [("h264_nvenc", "NVIDIA NVENC"), ("h264_amf", "AMD AMF"), ("h264_qsv", "Intel QuickSync")]


@lru_cache(maxsize=1)
def detect_encoders() -> list[dict]:
    """Probe each GPU encoder with a 1-frame encode; the list always ends with libx264 (CPU)."""
    out = []
    for codec, label in GPU_ENCODERS:
        cmd = [ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
               "-frames:v", "1", "-c:v", codec, "-f", "null", "-"]
        try:
            ok = subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            ok = False
        out.append({"codec": codec, "label": label, "available": ok})
    out.append({"codec": "libx264", "label": "CPU (x264)", "available": True})
    return out


def pick_encoder(preference: str = "auto") -> str:
    encs = detect_encoders()
    if preference != "auto" and any(e["codec"] == preference and e["available"] for e in encs):
        return preference
    return next(e["codec"] for e in encs if e["available"])


def encoder_args(codec: str, quality: str = "high") -> list[str]:
    q = {"high": 20, "medium": 24, "fast": 28}.get(quality, 22)
    if codec == "h264_nvenc":
        return ["-c:v", codec, "-preset", "p5", "-rc", "vbr", "-cq", str(q), "-b:v", "0"]
    if codec == "h264_amf":
        return ["-c:v", codec, "-quality", "quality", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q)]
    if codec == "h264_qsv":
        return ["-c:v", codec, "-global_quality", str(q), "-preset", "medium"]
    return ["-c:v", "libx264", "-preset", "veryfast" if quality == "fast" else "medium", "-crf", str(q)]


def probe(path: Path) -> dict:
    """Width, height, duration, fps, has_audio."""
    cmd = [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    info = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    num, den = (v.get("r_frame_rate", "30/1").split("/") if v else ("30", "1"))
    return {"width": int(v["width"]) if v else 0, "height": int(v["height"]) if v else 0,
            "duration": float(info["format"].get("duration", 0)),
            "fps": float(num) / float(den or 1),
            "has_audio": any(s["codec_type"] == "audio" for s in info["streams"])}


def filter_path(p: Path | str) -> str:
    """Escape a path for use inside an FFmpeg filter argument (Windows drive colons, quotes).
    Prefer render.py's approach (relative names inside a work folder), which avoids escaping entirely."""
    s = str(Path(p).resolve()).replace("\\", "/")
    return s.replace(":", r"\:").replace("'", r"\'")


def default_font() -> Path | None:
    """Bundled font first, then common system fonts."""
    candidates = [
        *[root / "assets" / "fonts" / "Anton-Regular.ttf" for root in _ROOTS],
        Path("C:/Windows/Fonts/impact.ttf"), Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("/System/Library/Fonts/Supplemental/Impact.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    return next((c for c in candidates if c.exists()), None)
