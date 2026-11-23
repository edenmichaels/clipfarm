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
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import layout as L
from .captions import build_track, fonts_dir
from .ffmpeg_util import encoder_args, ffmpeg_bin, pick_encoder, probe
from .. import proc

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
    # v0.5 edit-project extras (all clip time)
    template: dict | None = None         # full caption template (overrides caption_style)
    captions_enabled: bool = True
    layout_segments: list[tuple[float, str]] = field(default_factory=list)
    zooms: list[dict] = field(default_factory=list)        # [{t, amount, dur, kind: punch|pulse}]
    flashes: list[tuple[float, float]] = field(default_factory=list)
    shakes: list[tuple[float, float]] = field(default_factory=list)
    sfx: list[dict] = field(default_factory=list)          # [{path, t, gain_db}]
    speech_gain_db: float = 0.0
    enhance: bool = False                # speech clarity (high-pass + gentle compression)
    denoise: bool = False                # background noise reduction
    music_volume_db: float = 0.0
    scale_to: tuple[int, int] | None = None                # proxy previews, e.g. (540, 960)
    max_bitrate: str | None = None       # export presets
    srt_out: Path | None = None
    base: Path | None = None             # reuse an existing base render
    encoder: str = "auto"
    quality: str = "high"
    # v0.7 effects + brand (all clip time)
    emojis: list[dict] = field(default_factory=list)       # [{emoji, t, dur, size}] reaction emojis
    broll: list[dict] = field(default_factory=list)        # [{path, t, dur}] image or video cut-aways
    logo: dict | None = None             # {path, pos: tl|tr|bl|br, opacity, size}
    cards: list[dict] = field(default_factory=list)        # [{text, start, end, bg, fg}] intro/outro cards
    progress_color: str = "FFE500"
    credit_pos: str | None = None        # top | bottom (None = automatic)
    caption_position: str | None = None  # Style: seam | upper | centre | lower (None = layout default)
    cam_h: int = 640                     # facecam height in the split layout (Style: 35-45 % of 1920)
    title_box: str = "outline"           # Style title look: white_box | black_box | outline | none
    title_pos: str | None = None         # top | seam (None = layout default)
    # v0.8.3 Editor layers (all in output time)
    text_layers: list[dict] = field(default_factory=list)   # [{text,start,end,x,y (0-1),size,color,box,rotate}]
    stickers: list[dict] = field(default_factory=list)      # image stickers [{path,start,end,x,y (0-1),size,rotate}]
    color: dict | None = None            # {brightness -0.3..0.3, contrast 0.5..1.5, saturation 0..2, warmth -1..1}
    transform: dict | None = None        # {crop: {l,t,r,b fractions}, zoom 1..2, x/y pan -1..1, rotate degrees}
    transitions: list[dict] = field(default_factory=list)   # [{t, kind: fade|flash|zoom|whip, dur}]
    audio_keys: dict = field(default_factory=dict)          # {"speech": [{t, db}], "music": [{t, db}]}
    fps: int = 30


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return proc.run(cmd, capture_output=True, text=True, cwd=cwd)


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
        if spec.denoise:
            voice += ",afftdn=nr=12:nf=-30"
        if spec.enhance:
            voice += ",highpass=f=90,acompressor=threshold=0.08:ratio=3:attack=10:release=150:makeup=2"
        if spec.speech_gain_db:
            voice += f",volume={spec.speech_gain_db:.2f}dB"
        if (ke := keys_expr(spec.audio_keys.get("speech"))):
            voice += f",volume='pow(10,({ke})/20)':eval=frame"
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
        level = spec.music_level_lufs - (spec.music_track_lufs if spec.music_track_lufs is not None else -14.0) \
            + spec.music_volume_db
        speech = merge_intervals(spec.speech, 0.15)
        sw = merge_intervals(spec.swells)
        sp_env, sw_env = _trapezoid(speech, 0.12), _trapezoid(sw, 0.3)
        mk = keys_expr(spec.audio_keys.get("music"))
        gain = (f"if(lt(t,{spec.music_entry:.3f}),-90,"
                f"-{spec.duck_db:.2f}*({sp_env})+{spec.swell_db:.2f}*({sw_env})*(1-({sp_env})){f'+({mk})' if mk else ''})")
        fade_in = f"afade=t=in:st={spec.music_entry:.3f}:d={0.06 if spec.music_entry > 0 else 0.6}"
        chains.append(f"[{inputs}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,"
                      f"{f'adelay={int(spec.music_delay * 1000)}:all=1,atrim=0:{dur:.3f},' if spec.music_delay > 0 else ''}"
                      f"volume={level:.2f}dB,volume='pow(10,({gain})/20)':eval=frame,{fade_in},"
                      f"afade=t=out:st={max(0.0, dur - 1.0):.3f}:d=1[music]")
        mix.append("[music]")
        inputs += 1
    for k, fx in enumerate(spec.sfx[:16]):
        if not Path(fx["path"]).exists():
            continue
        cmd += ["-i", str(fx["path"])]
        chains.append(f"[{inputs}:a]aresample=48000,aformat=channel_layouts=stereo,volume={float(fx.get('gain_db', 0)):.1f}dB,"
                      f"adelay={int(max(0.0, float(fx['t'])) * 1000)}:all=1[sfx{k}]")
        mix.append(f"[sfx{k}]")
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
        parts.append("if(lt(t,1.5),0.04*t,0.06)")
    for b in spec.beats[:8]:
        parts.append(f"0.035*exp(-pow((t-{b:.3f})/0.09,2))")
    for z in spec.zooms[:24]:
        amt, t0, d = float(z.get("amount", 0.12)), float(z["t"]), max(0.2, float(z.get("dur", 1.2)))
        if amt >= 1.0:          # older projects stored a scale factor (1.16 = +16 %); it used to zoom to 216 %
            amt -= 1.0
        amt = max(0.0, min(0.6, amt))
        if z.get("kind") == "pulse":
            parts.append(f"{amt:.3f}*exp(-pow((t-{t0:.3f})/0.1,2))")
        else:   # punch-in: snap in, hold, ease out
            parts.append(f"{amt:.3f}*if(between(t,{t0:.3f},{t0 + d:.3f}),min(1,(t-{t0:.3f})/0.08),"
                         f"if(between(t,{t0 + d:.3f},{t0 + d + 0.3:.3f}),1-(t-{t0 + d:.3f})/0.3,0))")
    for a, b in spec.shakes[:12]:
        parts.append(f"0.05*between(t,{a:.3f},{b:.3f})")      # room for the shake
    if not parts:
        return None
    z = "1+" + "+".join(parts)
    sx = "+".join(f"between(t,{a:.3f},{b:.3f})*18*sin(t*71)" for a, b in spec.shakes[:12]) or "0"
    sy = "+".join(f"between(t,{a:.3f},{b:.3f})*14*cos(t*83)" for a, b in spec.shakes[:12]) or "0"
    # both sides computed explicitly (no -2 rounding), never below 1080x1920, and a final fixed-size scale so
    # FFmpeg builds that size the crop from a different first frame can't fail with "too big" (seen on Windows)
    return (f"scale=w='max(1080,trunc(1080*({z})/2)*2)':h='max(1920,trunc(1920*({z})/2)*2)':eval=frame,"
            f"crop=w='min(iw,1080)':h='min(ih,1920)':x='max(0,min(iw-1080,(iw-1080)/2+{sx}))':"
            f"y='max(0,min(ih-1920,(ih-1920)/2+{sy}))',scale=1080:1920")


def keys_expr(keys: list[dict], default: float = 0.0) -> str | None:
    """Piecewise-linear dB envelope from [{t, db}] keyframes (held before the first / after the last)."""
    pts = sorted((float(k["t"]), max(-60.0, min(12.0, float(k["db"])))) for k in keys or [] if "t" in k and "db" in k)
    if not pts:
        return None
    expr = f"{pts[-1][1]:.2f}"
    for (t0, d0), (t1, d1) in reversed(list(zip(pts, pts[1:]))):
        span = max(1e-3, t1 - t0)
        expr = f"if(lt(t,{t1:.3f}),{d0:.2f}+({d1 - d0:.2f})*(t-{t0:.3f})/{span:.3f},{expr})"
    return f"if(lt(t,{pts[0][0]:.3f}),{pts[0][1]:.2f},{expr})"


def _tri(t0: float, d: float) -> str:
    return f"max(0,1-abs(t-{t0:.3f})/{max(0.04, d / 2):.3f})"


def transform_filter(tf: dict | None) -> str | None:
    """Crop (fractions per side) -> fill 1080x1920 -> zoom/pan -> rotate. None when nothing is set."""
    if not tf:
        return None
    c = tf.get("crop") or {}
    l, t_, r, b = (max(0.0, min(0.45, float(c.get(k, 0)))) for k in ("l", "t", "r", "b"))
    z = max(1.0, min(2.0, float(tf.get("zoom", 1.0))))
    px, py = (max(-1.0, min(1.0, float(tf.get(k, 0)))) for k in ("x", "y"))
    rot = max(-45.0, min(45.0, float(tf.get("rotate", 0))))
    if not any((l, t_, r, b)) and z == 1.0 and not rot:
        return None
    parts = []
    if any((l, t_, r, b)):
        parts.append(f"crop=iw*{1 - l - r:.4f}:ih*{1 - t_ - b:.4f}:iw*{l:.4f}:ih*{t_:.4f},"
                     "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920")
    if z > 1.0:
        w, h = int(1080 * z) // 2 * 2, int(1920 * z) // 2 * 2
        parts.append(f"scale={w}:{h},crop=1080:1920:{(w - 1080) / 2 * (1 + px):.1f}:{(h - 1920) / 2 * (1 + py):.1f}")
    if rot:
        # rotate, then scale up just enough that no black corners show
        import math
        a = math.radians(abs(rot))
        k = math.cos(a) + max(1080 / 1920, 1920 / 1080) * math.sin(a)
        parts.append(f"rotate={math.radians(rot):.5f}:c=black,scale={int(1080 * k) // 2 * 2}:{int(1920 * k) // 2 * 2},"
                     "crop=1080:1920")
    return ",".join(parts)


def color_filter(color: dict | None, transitions: list[dict]) -> str | None:
    """Colour grade + dip/flash transitions in ONE eq pass (brightness is evaluated per frame)."""
    c = color or {}
    br = max(-0.3, min(0.3, float(c.get("brightness", 0))))
    ct = max(0.5, min(1.5, float(c.get("contrast", 1))))
    sat = max(0.0, min(2.0, float(c.get("saturation", 1))))
    warm = max(-1.0, min(1.0, float(c.get("warmth", 0))))
    dips = [f"-{_tri(float(x['t']), float(x.get('dur', 0.4)))}" for x in transitions or [] if x.get("kind") == "fade"]
    flashes = [f"+0.8*{_tri(float(x['t']), float(x.get('dur', 0.25)))}" for x in transitions or [] if x.get("kind") == "flash"]
    out = []
    if br or ct != 1 or sat != 1 or dips or flashes:
        expr = f"{br:.3f}{''.join(dips)}{''.join(flashes)}"
        out.append(f"eq=brightness='{expr}':contrast={ct:.3f}:saturation={sat:.3f}:eval=frame")
    if warm:
        out.append(f"colorbalance=rs={0.12 * warm:.3f}:gs={0.03 * warm:.3f}:bs={-0.12 * warm:.3f}:"
                   f"rm={0.08 * warm:.3f}:bm={-0.08 * warm:.3f}")
    return ",".join(out) or None


def track_for(spec: RenderSpec, meta: dict, out: Path):
    """The caption/title/text-layer track for a render (also used by the Editor to place its caption handle)."""
    dur = meta["duration"]
    return build_track(spec.words if spec.captions_enabled else [], out,
                       spec.template or spec.caption_style, spec.words_per_chunk, layout_segments=spec.layout_segments,
                       offset=spec.start, layout=meta["layout"], src_w=meta.get("src_w", 1920),
                       src_h=meta.get("src_h", 1080), keep_clear=tuple(spec.keep_clear),
                       nudge=spec.caption_nudge, uppercase=spec.uppercase, profanity=spec.profanity,
                       fillers=spec.fillers, emoji=spec.emoji, hook=spec.hook, hook_sec=spec.hook_sec,
                       credit=spec.credit, duration=dur, credit_pos=spec.credit_pos, cam_h=spec.cam_h,
                       position=spec.caption_position, title_box=spec.title_box, title_pos=spec.title_pos,
                       text_layers=spec.text_layers)


def finish(spec: RenderSpec, base: Path, meta: dict, out: Path | None = None, music: bool = True,
           frame_at: float | None = None) -> dict:
    """The final pass: zoom/shake, captions (libass), emojis, flashes, B-roll, logo, cards, progress bar, audio mix.
    frame_at=t renders ONE still frame (PNG at `out`) through exactly the same video chain, so the Editor's
    preview and the export can't differ."""
    out = Path(out or spec.out).resolve()       # FFmpeg runs in a temp folder, so paths must be absolute
    base = Path(base).resolve()
    dur = meta["duration"]
    work = Path(tempfile.mkdtemp(prefix="cff_"))
    try:
        fd = fonts_dir()
        if fd:
            shutil.copytree(fd, work / "fonts", dirs_exist_ok=True)
        track = track_for(spec, meta, work / "captions.ass")
        if frame_at is None:
            shutil.copy2(track.path, out.with_suffix(".ass"))
            shutil.copy2(track.path.with_suffix(".events.json"), out.with_suffix(".events.json"))
            mspec = spec if music else RenderSpec(**{**spec.__dict__, "music": None})
            mix = build_audio(mspec, base, meta, track.bleeps, work)
            ln = measure_loudness(mix)
            cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", str(base), "-i", str(mix)]
            chain, last, n_in = [], "0:v", 2
        else:
            # seek the input but keep its timestamps, so every timed filter (captions, overlays) sees the real t
            t = max(0.0, min(float(frame_at), dur - 0.02))
            cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{t:.3f}", "-copyts", "-i", str(base)]
            chain, last, n_in = [], "0:v", 1

        def add(f: str):
            nonlocal last
            nxt = f"v{len(chain)}"
            chain.append(f"[{last}]{f}[{nxt}]")
            last = nxt

        if (tf := transform_filter(spec.transform)):
            add(tf)
        if (z := _zoom_expr(spec)):
            add(z)
        if (cf := color_filter(spec.color, spec.transitions)):
            add(cf)
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
        for k, (a, b) in enumerate(spec.flashes[:12]):
            chain.append(f"color=c=white:s=1080x1920:r={spec.fps}:d={dur:.3f},format=rgba,"
                         f"colorchannelmixer=aa=0.75[fl{k}]")
            add(f"[fl{k}]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'")
        for k, b in enumerate(spec.broll[:6]):
            bp = Path(b["path"])
            if not bp.exists():
                continue
            t0, t1 = float(b["t"]), float(b["t"]) + float(b.get("dur", 2.0))
            if bp.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(bp)]
                chain.append(f"[{n_in}:v]scale=900:-2,format=rgba[br{k}]")
            else:
                cmd += ["-t", f"{t1 - t0 + 0.1:.3f}", "-i", str(bp)]
                chain.append(f"[{n_in}:v]scale=900:-2,format=rgba,setpts=PTS-STARTPTS+{t0:.3f}/TB[br{k}]")
            add(f"[br{k}]overlay=x=(W-w)/2:y=330:eof_action=pass:enable='between(t,{t0:.3f},{t1:.3f})'")
            n_in += 1
        for k, e in enumerate(spec.emojis[:20] if edir else []):
            code = "_".join(f"{ord(c):x}" for c in e["emoji"] if ord(c) != 0xFE0F)
            png = edir / f"emoji_u{code}.png"
            if not png.exists():
                continue
            size = int(e.get("size", 220))
            cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(png)]
            t0, t1 = float(e["t"]), float(e["t"]) + float(e.get("dur", 1.2))
            rot = float(e.get("rotate", 0) or 0)
            rf = f",rotate={rot * 3.14159265 / 180:.4f}:c=none:ow=rotw({rot * 3.14159265 / 180:.4f}):oh=roth({rot * 3.14159265 / 180:.4f})" if rot else ""
            chain.append(f"[{n_in}:v]scale={size}:{size},format=rgba{rf}[ex{k}]")
            add(f"[ex{k}]overlay=x={int(e.get('x', 540))}-w/2:y={int(e.get('y', 560))}-h/2:"
                f"enable='between(t,{t0:.3f},{t1:.3f})'")
            n_in += 1
        for k, st in enumerate(spec.stickers[:12]):
            sp = Path(st.get("path", ""))
            if not sp.exists():
                continue
            size = max(40, min(1080, int(st.get("size", 300))))
            rot = float(st.get("rotate", 0) or 0) * 3.14159265 / 180
            rf = f",rotate={rot:.4f}:c=none:ow=rotw({rot:.4f}):oh=roth({rot:.4f})" if rot else ""
            cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(sp)]
            chain.append(f"[{n_in}:v]scale={size}:-2,format=rgba{rf}[stk{k}]")
            x, y = float(st.get("x", 0.5)) * 1080, float(st.get("y", 0.5)) * 1920
            add(f"[stk{k}]overlay=x={x:.0f}-w/2:y={y:.0f}-h/2:enable='between(t,{float(st['start']):.3f},{float(st['end']):.3f})'")
            n_in += 1
        if spec.logo and Path(spec.logo.get("path", "")).exists():
            lg = spec.logo
            size = int(lg.get("size", 150))
            op = max(0.1, min(1.0, float(lg.get("opacity", 0.85))))
            pos = {"tl": ("70", "230"), "tr": ("W-w-160", "230"), "bl": ("70", "1420-h-20"),
                   "br": ("W-w-160", "1420-h-20")}.get(lg.get("pos", "tr"), ("W-w-160", "230"))
            cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(lg["path"])]
            chain.append(f"[{n_in}:v]scale={size}:-2,format=rgba,colorchannelmixer=aa={op:.2f}[logo]")
            add(f"[logo]overlay=x={pos[0]}:y={pos[1]}")
            n_in += 1
        for k, c in enumerate(spec.cards[:2]):
            png = work / f"card{k}.png"
            if not _card_png(c, png):
                continue
            cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(png)]
            t0, t1 = float(c["start"]), float(c["end"])
            chain.append(f"[{n_in}:v]format=rgba,fade=t=in:st={t0:.3f}:d=0.15:alpha=1,"
                         f"fade=t=out:st={max(t0, t1 - 0.15):.3f}:d=0.15:alpha=1[cd{k}]")
            add(f"[cd{k}]overlay=0:0:enable='between(t,{t0:.3f},{t1:.3f})'")
            n_in += 1
        if spec.progress_bar:
            pc = re.sub(r"[^0-9A-Fa-f]", "", spec.progress_color or "FFE500")[:6] or "FFE500"
            chain.append(f"color=c=0x{pc}:s=1080x14:r={spec.fps}:d={dur:.3f}[pbar]")
            add(f"[pbar]overlay=x='-w+w*t/{dur:.3f}':y=H-h:eof_action=pass")
        if spec.scale_to:
            add(f"scale={spec.scale_to[0]}:{spec.scale_to[1]}")
        if frame_at is not None:
            res = _run(cmd + ["-filter_complex", ";".join(chain), "-map", f"[{last}]", "-frames:v", "1",
                              "-update", "1", str(out)], cwd=work)
            if res.returncode != 0 or not out.exists():
                raise RuntimeError(f"FFmpeg frame failed: {res.stderr[-800:]}")
            return {"path": str(out), "t": t, "events": track.events, "title": track.title}
        if spec.srt_out:
            ev = track.events if spec.captions_enabled else build_track(
                spec.words, work / "srt.ass", spec.template or spec.caption_style, spec.words_per_chunk,
                offset=spec.start, layout=meta["layout"], src_w=meta.get("src_w", 1920), src_h=meta.get("src_h", 1080),
                uppercase=False, profanity=spec.profanity, fillers=spec.fillers).events
            write_srt(ev, spec.srt_out)
        af = (f"loudnorm=I={spec.target_lufs}:TP=-1.5:LRA=11:linear=true:measured_I={ln['input_i']}:"
              f"measured_TP={ln['input_tp']}:measured_LRA={ln['input_lra']}:measured_thresh={ln['input_thresh']}:"
              f"offset={ln['target_offset']},aresample=48000")
        codec = pick_encoder(spec.encoder)

        def full(c):
            return cmd + ["-filter_complex", ";".join(chain), "-map", f"[{last}]", "-map", "1:a", "-af", af,
                          "-c:a", "aac", "-b:a", "192k", *encoder_args(c, spec.quality),
                          *(["-maxrate", spec.max_bitrate, "-bufsize", spec.max_bitrate] if spec.max_bitrate else []),
                          "-pix_fmt", "yuv420p",
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
            "sha256": sha256(out), "events": track.events, "title": track.title, "bleeps": track.bleeps,
            "music": str(spec.music) if music and spec.music else None, **meta}


def write_srt(events: list[dict], out: Path) -> Path:
    """Captions as a separate .srt (export presets that don't burn them in)."""
    def ts(t):
        ms = int(round(t * 1000))
        h, r = divmod(ms, 3600000)
        m, r = divmod(r, 60000)
        sec, ms = divmod(r, 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
    blocks, seen = [], set()
    for e in events:
        text = "\n".join(" ".join(r) for r in e["rows"])
        key = (text, e["y"])
        if key in seen and blocks and blocks[-1][2] == text:
            blocks[-1][1] = e["end"]
            continue
        seen.add(key)
        blocks.append([e["start"], e["end"], text])
    out.write_text("\n".join(f"{i + 1}\n{ts(a)} --> {ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(blocks)),
                   encoding="utf-8")
    return out


def _card_png(card: dict, out: Path) -> bool:
    """A full-frame intro/outro card: brand background and big centred text, drawn with a bundled font."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        from .captions import FONTS, fonts_dir
    except ImportError:
        return False

    def rgb(h: str, d: str) -> tuple[int, int, int]:
        h = re.sub(r"[^0-9A-Fa-f]", "", h or d)[:6] or d
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    img = Image.new("RGBA", (1080, 1920), (*rgb(card.get("bg", ""), "111111"), 240))
    fd = fonts_dir()
    text = str(card.get("text") or "").strip()[:80]
    if text:
        size = 96
        font = ImageFont.truetype(str(fd / FONTS["Montserrat Black"]), size) if fd else ImageFont.load_default()
        words, lines, cur = text.split(), [], ""
        for w in words:
            nxt = (cur + " " + w).strip()
            if font.getlength(nxt) > 800 and cur:
                lines.append(cur)
                cur = w
            else:
                cur = nxt
        lines.append(cur)
        d = ImageDraw.Draw(img)
        y = 960 - len(lines) * size * 0.6
        for ln in lines:
            d.text((540, y), ln, font=font, fill=(*rgb(card.get("fg", ""), "FFFFFF"), 255), anchor="ma")
            y += size * 1.2
    img.save(out)
    return True


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
