"""Render one vertical short: cut -> reframe -> zoom -> hook -> captions -> progress bar ->
credit -> audio polish -> encode (GPU if available) -> thumbnail.
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import layout as L
from .captions import build_ass
from .ffmpeg_util import (default_font, encoder_args, ffmpeg_bin, filter_path, pick_encoder, probe)


@dataclass
class RenderSpec:
    source: Path
    start: float
    end: float
    out: Path
    layout: str = "auto"                 # auto | split | crop | blur_fill
    facecam: L.Box | None = None         # saved per streamer; detected if None and layout needs it
    words: list[dict] = field(default_factory=list)   # transcript words, source-relative
    caption_style: str = "bold_pop"
    words_per_chunk: int = 3
    hook: str | None = None
    hook_sec: float = 2.0
    credit: str | None = None            # e.g. "twitch.tv/streamer" (required by most programs)
    progress_bar: bool = True
    punch_zoom: bool = True              # subtle zoom-in over the first 1.5s
    music: Path | None = None            # royalty-free track, mixed quietly
    music_volume: float = 0.12
    encoder: str = "auto"
    quality: str = "high"
    fps: int = 30


def _dt(text: str) -> str:
    """Escape text for drawtext."""
    return (text.replace("\\", "\\\\").replace(":", r"\:").replace("'", "’")
            .replace("%", r"\%").replace(",", r"\,"))


def build_command(spec: RenderSpec, ass_path: Path | None) -> tuple[list[str], str, dict]:
    info = probe(spec.source)
    dur = max(1.0, spec.end - spec.start)
    layout_name = spec.layout
    facecam, track = spec.facecam, None
    if layout_name in ("auto", "split") and facecam is None:
        try:
            facecam = L.detect_facecam(spec.source, spec.start, spec.end)
        except Exception:
            facecam = None
    if layout_name == "auto":
        layout_name = "split" if facecam else "crop"
    if layout_name == "split" and not facecam:
        layout_name = "blur_fill"
    if layout_name == "crop":
        try:
            track = L.track_action(spec.source, spec.start, spec.end)
        except Exception:
            track = None

    graph, last = L.filtergraph(layout_name, info["width"], info["height"], facecam, track)
    chain = [graph]

    def add(f: str):
        nonlocal last
        nxt = f"v{len(chain)}"
        chain.append(f"[{last}]{f}[{nxt}]")
        last = nxt

    add(f"fps={spec.fps}")
    if spec.punch_zoom:
        # 1.0 -> 1.06 over 1.5 s then hold; scale+crop keeps it cheap and frame-accurate.
        add("scale=w='trunc(1080*if(lt(t,1.5),1+0.04*t,1.06)/2)*2':h=-2:eval=frame,crop=1080:1920")
    font = default_font()
    fontarg = f"fontfile='{filter_path(font)}':" if font else ""
    if ass_path:
        fontsdir = f":fontsdir='{filter_path(font.parent)}'" if font else ""
        add(f"ass='{filter_path(ass_path)}'{fontsdir}")
    if spec.hook:
        size = max(44, min(84, int(1500 / max(len(spec.hook), 1) * 1.6)))  # shrink long hooks to fit
        add(f"drawtext={fontarg}text='{_dt(spec.hook)}':fontsize={size}:fontcolor=white:borderw=6:bordercolor=black"
            f":box=1:boxcolor=black@0.35:boxborderw=24:x=(w-text_w)/2:y=h*0.13"
            f":enable='lt(t,{spec.hook_sec})'")
    if spec.credit:
        add(f"drawtext={fontarg}text='{_dt(spec.credit)}':fontsize=40:fontcolor=white@0.85:borderw=3"
            f":bordercolor=black@0.7:x=(w-text_w)/2:y=h*0.05")
    if spec.progress_bar:
        add(f"drawbox=x=0:y=ih-14:w='max(2,iw*t/{dur:.2f})':h=14:color=0xFFE500@0.9:t=fill")

    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{spec.start:.3f}", "-t", f"{dur:.3f}", "-i", str(spec.source)]
    audio_map: list[str] = []
    if spec.music and spec.music.exists():
        cmd += ["-stream_loop", "-1", "-i", str(spec.music)]
    if info["has_audio"]:
        a = "[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
        if spec.music and spec.music.exists():
            chain.append(f"{a}[voice];[1:a]volume={spec.music_volume},aresample=48000[bgm];"
                         f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0[aout]")
        else:
            chain.append(f"{a}[aout]")
        audio_map = ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
    codec = pick_encoder(spec.encoder)
    cmd += ["-filter_complex", ";".join(chain), "-map", f"[{last}]", *audio_map,
            *encoder_args(codec, spec.quality), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-t", f"{dur:.3f}", str(spec.out)]
    return cmd, codec, {"layout": layout_name, "facecam": facecam.__dict__ if facecam else None}


def render(spec: RenderSpec) -> dict:
    spec.out.parent.mkdir(parents=True, exist_ok=True)
    ass_path = None
    if spec.words:
        ass_path = build_ass(spec.words, spec.out.with_suffix(".ass"), spec.caption_style,
                             spec.words_per_chunk, offset=spec.start)
    cmd, codec, meta = build_command(spec, ass_path)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 and codec != "libx264":
        # GPU driver hiccup: retry once on CPU rather than failing the job.
        spec.encoder = "libx264"
        cmd, codec, meta = build_command(spec, ass_path)
        res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {res.stderr[-1500:]}")
    thumb = spec.out.with_suffix(".jpg")
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-ss", "1.0", "-i", str(spec.out), "-frames:v", "1",
                    "-vf", "scale=360:-2", str(thumb)], check=False)
    return {"path": str(spec.out), "thumb": str(thumb) if thumb.exists() else None, "encoder": codec,
            "duration": spec.end - spec.start, "sha256": sha256(spec.out), **meta}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
