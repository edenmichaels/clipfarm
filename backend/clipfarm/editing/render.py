"""Render one vertical short in two passes.

Pass 1, base (slow, once per clip): cut -> reframe to 1080x1920 (split / crop / blur_fill) -> original audio.
Pass 2, finish (fast, repeatable): punch-in zoom + zoom pulses on the music's beats -> captions, hook and
credit (libass) -> optional emoji -> progress bar; audio = stream audio (with bleeps) + music, ducked
under speech and swelling on reactions, then loudness-normalised to -14 LUFS (measured, then applied).

Keeping the base means Review can nudge captions, fix a word, change music or export a "no music"
version (for adding a trending sound in TikTok/Instagram) without re-cutting the source.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import layout as L
from .captions import build_track, fonts_dir
from .ffmpeg_util import encoder_args, ffmpeg_bin, pick_encoder, probe

EMOJI_DIRS = ("assets/emoji",)


@dataclass
class RenderSpec:
    source: Path
    start: float
    end: float
    out: Path
    layout: str = "auto"                 # auto | split | crop | blur_fill
    facecam: L.Box | None = None
    words: list[dict] = field(default_factory=list)   # transcript words, source time
    caption_style: str = "bold_pop"
    words_per_chunk: int | None = None
    caption_nudge: float = 0.0           # px; negative = up
    uppercase: bool | None = None
    profanity: str = "keep"              # keep | mask | bleep
    fillers: str = "remove"              # keep | remove
    emoji: bool = False
    keep_clear: tuple[str, ...] = ()     # campaign on-screen rules, e.g. ("streamer_overlay",)
    hook: str | None = None
    hook_sec: float = 2.2
    credit: str | None = None
    progress_bar: bool = True
    punch_zoom: bool = True
    # music (licensed tracks only -- see clipfarm/music); None = original audio only
    music: Path | None = None
    music_offset: float = 0.0            # seconds into the track at clip t=0
    music_entry: float = 0.0             # clip time the music comes in (the payoff for a "drop")
    music_delay: float = 0.0             # clip seconds of silence before the track starts
    music_track_lufs: float | None = None
    music_level_lufs: float = -24.0      # music level when nobody's talking
    duck_db: float = 15.0                # how far music drops under speech
    swell_db: float = 4.0                # lift on reactions / action
    speech: list[tuple[float, float]] = field(default_factory=list)    # clip time
    swells: list[tuple[float, float]] = field(default_factory=list)    # clip time
    beats: list[float] = field(default_factory=list)                   # clip time, zoom pulses
    target_lufs: float = -14.0
    base: Path | None = None             # reuse an existing base render
    encoder: str = "auto"
    quality: str = "high"
    fps: int = 30


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


# ---------------------------------------------------------------- pass 1
def base_path(out: Path) -> Path:
    return out.with_name(out.stem + ".base.mp4")


def render_base(spec: RenderSpec) -> tuple[Path, dict]:
    info = probe(spec.source)
    dur = max(1.0, spec.end - spec.start)
    layout_name, facecam, track = spec.layout, spec.facecam, None
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
    graph += f";[{last}]fps={spec.fps},format=yuv420p[vb]"
    out = base_path(spec.out)
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{spec.start:.3f}", "-t", f"{dur:.3f}", "-i", str(spec.source),
           "-filter_complex", graph, "-map", "[vb]"]
    if info["has_audio"]:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "256k", "-ar", "48000"]
    codec = pick_encoder(spec.encoder)
    tail = [*encoder_args(codec, "high"), "-pix_fmt", "yuv420p", "-t", f"{dur:.3f}", str(out)]
    res = _run(cmd + tail)
    if res.returncode != 0 and codec != "libx264":
        codec = "libx264"
        res = _run(cmd + [*encoder_args(codec, "high"), "-pix_fmt", "yuv420p", "-t", f"{dur:.3f}", str(out)])
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg failed (base): {res.stderr[-1500:]}")
    meta = {"layout": layout_name, "facecam": facecam.__dict__ if facecam else None, "src_w": info["width"],
            "src_h": info["height"], "has_audio": info["has_audio"], "duration": dur}
    out.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    return out, meta


# ---------------------------------------------------------------- audio
def _between_sum(iv: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in iv) or "0"


def _trapezoid(iv: list[tuple[float, float]], ramp: float) -> str:
    """0..1 envelope that is 1 inside any interval, ramping over `ramp` seconds at the edges."""
    if not iv:
        return "0"
    terms = [f"clip(min((t-{a - ramp:.3f})/{ramp:.3f},({b + ramp:.3f}-t)/{ramp:.3f}),0,1)" for a, b in iv]
    expr = terms[0]
    for t in terms[1:]:
        expr = f"max({expr},{t})"
    return expr


def merge_intervals(iv: list[tuple[float, float]], pad: float = 0.0, join: float = 0.3) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(iv):
        a, b = a - pad, b + pad
        if out and a <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(max(0.0, a), b) for a, b in out]


def music_gain_db(t: float, spec: RenderSpec) -> float:
    """Pure-Python twin of the FFmpeg expression (used by tests and the Review preview)."""
    if t < spec.music_entry:
        return -90.0
    speech = merge_intervals(spec.speech, 0.15)
    sw = merge_intervals(spec.swells)

    def env(iv, ramp):
        return max([max(0.0, min(1.0, min((t - (a - ramp)) / ramp, ((b + ramp) - t) / ramp))) for a, b in iv] or [0.0])
    return -spec.duck_db * env(speech, 0.12) + spec.swell_db * env(sw, 0.3) * (1 - env(speech, 0.12))


def build_audio(spec: RenderSpec, base: Path, meta: dict, bleeps: list[tuple[float, float]], work: Path) -> Path:
    """Stream audio (+ bleeps) + ducked music -> mix.wav (not yet loudness-normalised)."""
    dur = meta["duration"]
    cmd = [ffmpeg_bin(), "-y", "-v", "error"]
    inputs = 0
    chains = []
    if meta.get("has_audio"):
        cmd += ["-i", str(base)]
        voice = f"[{inputs}:a]aresample=48000,aformat=channel_layouts=stereo"
        if bleeps:
            voice += f",volume=0:enable='{_between_sum(bleeps)}'"
        chains.append(voice + "[voice]")
        inputs += 1
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        chains.append(f"[{inputs}:a]anull[voice]")
        inputs += 1
    mix = ["[voice]"]
    if bleeps:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "sine=frequency=1000:sample_rate=48000"]
        chains.append(f"[{inputs}:a]aformat=channel_layouts=stereo,volume=0.18,"
                      f"volume=0:enable='not({_between_sum(bleeps)})'[beep]")
        mix.append("[beep]")
        inputs += 1
    if spec.music and Path(spec.music).exists():
        cmd += ["-stream_loop", "-1", "-ss", f"{max(0.0, spec.music_offset):.3f}", "-i", str(spec.music)]
        level = spec.music_level_lufs - (spec.music_track_lufs if spec.music_track_lufs is not None else -14.0)
        speech = merge_intervals(spec.speech, 0.15)
        sw = merge_intervals(spec.swells)
        sp_env, sw_env = _trapezoid(speech, 0.12), _trapezoid(sw, 0.3)
        gain = (f"if(lt(t,{spec.music_entry:.3f}),-90,"
                f"-{spec.duck_db:.2f}*({sp_env})+{spec.swell_db:.2f}*({sw_env})*(1-({sp_env})))")
        fade_in = f"afade=t=in:st={spec.music_entry:.3f}:d={0.06 if spec.music_entry > 0 else 0.6}"
        chains.append(f"[{inputs}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,"
                      f"{f'adelay={int(spec.music_delay * 1000)}:all=1,atrim=0:{dur:.3f},' if spec.music_delay > 0 else ''}"
                      f"volume={level:.2f}dB,volume='pow(10,({gain})/20)':eval=frame,{fade_in},"
                      f"afade=t=out:st={max(0.0, dur - 1.0):.3f}:d=1[music]")
        mix.append("[music]")
        inputs += 1
    if len(mix) > 1:
        chains.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0:dropout_transition=0[mix]")
    else:
        chains.append("[voice]anull[mix]")
    out = work / "mix.wav"
    cmd += ["-filter_complex", ";".join(chains), "-map", "[mix]", "-t", f"{dur:.3f}", "-c:a", "pcm_s16le", str(out)]
    res = _run(cmd)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg failed (audio): {res.stderr[-1500:]}")
    return out


def measure_loudness(path: Path) -> dict:
    res = _run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-af",
                "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    txt = res.stderr[res.stderr.rfind("{"):res.stderr.rfind("}") + 1]
    return json.loads(txt)


def integrated_lufs(path: Path) -> float:
    res = _run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"])
    import re
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", res.stderr)
    return float(m[-1]) if m else float("nan")


# ---------------------------------------------------------------- pass 2
def _zoom_expr(spec: RenderSpec) -> str | None:
    parts = []
    if spec.punch_zoom:
        parts.append("if(lt(t,1.5),1+0.04*t,1.06)")
    for b in spec.beats[:8]:
        parts.append(f"0.035*exp(-pow((t-{b:.3f})/0.09,2))")
    if not parts:
        return None
    z = "+".join(parts) if spec.punch_zoom else "1+" + "+".join(parts)
    return f"scale=w='trunc(1080*({z})/2)*2':h=-2:eval=frame,crop=1080:1920"


def finish(spec: RenderSpec, base: Path, meta: dict, out: Path | None = None, music: bool = True) -> dict:
    out = out or spec.out
    dur = meta["duration"]
    work = Path(tempfile.mkdtemp(prefix="cff_"))
    try:
        fd = fonts_dir()
        if fd:
            shutil.copytree(fd, work / "fonts", dirs_exist_ok=True)
        track = build_track(spec.words, work / "captions.ass", spec.caption_style, spec.words_per_chunk,
                            offset=spec.start, layout=meta["layout"], src_w=meta.get("src_w", 1920),
                            src_h=meta.get("src_h", 1080), keep_clear=tuple(spec.keep_clear),
                            nudge=spec.caption_nudge, uppercase=spec.uppercase, profanity=spec.profanity,
                            fillers=spec.fillers, emoji=spec.emoji, hook=spec.hook, hook_sec=spec.hook_sec,
                            credit=spec.credit, duration=dur)
        shutil.copy2(track.path, out.with_suffix(".ass"))
        shutil.copy2(track.path.with_suffix(".events.json"), out.with_suffix(".events.json"))
        mspec = spec if music else RenderSpec(**{**spec.__dict__, "music": None})
        mix = build_audio(mspec, base, meta, track.bleeps, work)
        ln = measure_loudness(mix)

        cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", str(base), "-i", str(mix)]
        chain, last, n_in = [], "0:v", 2

        def add(f: str):
            nonlocal last
            nxt = f"v{len(chain)}"
            chain.append(f"[{last}]{f}[{nxt}]")
            last = nxt

        if (z := _zoom_expr(spec)):
            add(z)
        add("ass=captions.ass" + (":fontsdir=fonts" if fd else ""))
        edir = _emoji_dir()
        for e in track.emojis[:12] if edir else []:
            png = edir / f"emoji_u{e['code']}.png"
            if not png.exists():
                continue
            cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(png)]
            chain.append(f"[{n_in}:v]scale={e['size']}:{e['size']},format=rgba[e{n_in}]")
            add(f"[e{n_in}]overlay=x={e['x']}:y={e['y']}:enable='between(t,{e['start']:.3f},{e['end']:.3f})'")
            n_in += 1
        if spec.progress_bar:
            chain.append(f"color=c=0xFFE500:s=1080x14:r={spec.fps}:d={dur:.3f}[pbar]")
            add(f"[pbar]overlay=x='-w+w*t/{dur:.3f}':y=H-h:eof_action=pass")
        af = (f"loudnorm=I={spec.target_lufs}:TP=-1.5:LRA=11:linear=true:measured_I={ln['input_i']}:"
              f"measured_TP={ln['input_tp']}:measured_LRA={ln['input_lra']}:measured_thresh={ln['input_thresh']}:"
              f"offset={ln['target_offset']},aresample=48000")
        codec = pick_encoder(spec.encoder)

        def full(c):
            return cmd + ["-filter_complex", ";".join(chain), "-map", f"[{last}]", "-map", "1:a", "-af", af,
                          "-c:a", "aac", "-b:a", "192k", *encoder_args(c, spec.quality), "-pix_fmt", "yuv420p",
                          "-movflags", "+faststart", "-t", f"{dur:.3f}", str(out)]
        res = _run(full(codec), cwd=work)
        if res.returncode != 0 and codec != "libx264":
            codec = "libx264"
            res = _run(full(codec), cwd=work)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {res.stderr[-1500:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    thumb = out.with_suffix(".jpg")
    _run([ffmpeg_bin(), "-y", "-v", "error", "-ss", "1.0", "-i", str(out), "-frames:v", "1", "-vf", "scale=360:-2",
          str(thumb)])
    return {"path": str(out), "thumb": str(thumb) if thumb.exists() else None, "encoder": codec, "duration": dur,
            "sha256": sha256(out), "events": track.events, "bleeps": track.bleeps,
            "music": str(spec.music) if music and spec.music else None, **meta}


def _emoji_dir() -> Path | None:
    from .ffmpeg_util import _ROOTS
    for root in _ROOTS:
        if (root / "assets" / "emoji").exists():
            return root / "assets" / "emoji"
    return None


def render(spec: RenderSpec) -> dict:
    spec.out.parent.mkdir(parents=True, exist_ok=True)
    if spec.base and Path(spec.base).exists():
        base = Path(spec.base)
        meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    else:
        base, meta = render_base(spec)
    res = finish(spec, base, meta)
    res["base"] = str(base)
    return res


def export_without_music(spec: RenderSpec, out: Path) -> dict:
    """For adding a trending sound in TikTok/Instagram: same video and captions, stream audio only."""
    base = Path(spec.base) if spec.base else base_path(spec.out)
    meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    return finish(spec, base, meta, out=out, music=False)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
