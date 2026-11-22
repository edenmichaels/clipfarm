"""Render an edit project (EDL).

  base   = the cut list + layout keyframes, reframed to 1080x1920 (one FFmpeg pass). Cached by a hash
           of exactly those inputs, so caption / hook / music / zoom changes never redo it.
  finish = zooms, captions, hook, credit, effects, progress bar, music + SFX mix, -14 LUFS (render.finish).

Quality: "proxy" (fast, 540x960, for the Editor preview) or "export" (full quality, GPU encoder when
available: NVENC / AMF / QSV, CPU fallback).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import db
from ..editing import layout as L
from ..editing.ffmpeg_util import encoder_args, ffmpeg_bin, pick_encoder
from ..editing.render import RenderSpec, finish
from . import timeline as T
from .. import proc

W, H = 1080, 1920

PRESETS = {   # export presets per platform
    "tiktok": {"label": "TikTok", "max_sec": 600, "bitrate": "12M", "captions": "burned"},
    "shorts": {"label": "YouTube Shorts", "max_sec": 180, "bitrate": "12M", "captions": "burned"},
    "reels": {"label": "Instagram Reels", "max_sec": 180, "bitrate": "10M", "captions": "burned"},
    "x": {"label": "X", "max_sec": 140, "bitrate": "8M", "captions": "burned"},
    "srt": {"label": "Video + separate captions (.srt)", "max_sec": 600, "bitrate": "12M", "captions": "srt"},
}


# ---- layouts as filter graphs with named pads --------------------------------------------------------
def piece_graph(lay: str, inp: str, out: str, sw: int, sh: int, cam: dict | None, path: list) -> str:
    keys = [(float(t), float(x)) for t, x in (path or [[0, 0.5]])]
    if lay == "split" and cam:
        cam_h, game_h = 640, H - 640
        fx, fy, fw, fh = int(cam["x"] * sw), int(cam["y"] * sh), int(cam["w"] * sw), int(cam["h"] * sh)
        gw = min(sw, int(sh * W / game_h)) // 2 * 2
        return (f"[{inp}]split=2[{out}a][{out}b];"
                f"[{out}a]crop={fw}:{fh}:{fx}:{fy},scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[{out}c];"
                f"[{out}b]crop={gw}:{sh}:'{L._lerp_expr(keys, sw, gw)}':0,scale={W}:{game_h}:force_original_aspect_ratio=increase,"
                f"crop={W}:{game_h}[{out}g];[{out}c][{out}g]vstack=inputs=2,setsar=1[{out}]")
    if lay in ("crop", "speaker") or (lay == "pip" and not cam):
        cw = min(sw, int(sh * 9 / 16)) // 2 * 2
        return f"[{inp}]crop={cw}:{sh}:'{L._lerp_expr(keys, sw, cw)}':0,scale={W}:{H},setsar=1[{out}]"
    if lay == "pip":
        cw = min(sw, int(sh * 9 / 16)) // 2 * 2
        fx, fy, fw, fh = int(cam["x"] * sw), int(cam["y"] * sh), int(cam["w"] * sw), int(cam["h"] * sh)
        return (f"[{inp}]split=2[{out}a][{out}b];"
                f"[{out}a]crop={cw}:{sh}:'{L._lerp_expr(keys, sw, cw)}':0,scale={W}:{H},setsar=1[{out}g];"
                f"[{out}b]crop={fw}:{fh}:{fx}:{fy},scale=420:-2,pad=iw+8:ih+8:4:4:white[{out}c];"
                f"[{out}g][{out}c]overlay=x=60:y=250[{out}]")
    return (f"[{inp}]split=2[{out}a][{out}b];"
            f"[{out}a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2,eq=brightness=-0.08[{out}bg];"
            f"[{out}b]scale={W}:-2[{out}fg];[{out}bg][{out}fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")


def pieces(edl: dict) -> list[tuple[float, float, dict]]:
    """Kept segments split at layout keyframes: [(src_a, src_b, keyframe)]."""
    kfs = sorted(edl["layout"].get("keyframes") or [], key=lambda k: k["t"]) or \
        [{"t": edl["range"][0], "layout": "blur_fill", "cam": None, "path": [[0, 0.5]]}]
    out = []
    for a, b in T.kept(edl):
        cuts = sorted({a, b, *[k["t"] for k in kfs if a < k["t"] < b]})
        for p, q in zip(cuts, cuts[1:]):
            if q - p < T.MIN_SEG:
                continue
            kf = next((k for k in reversed(kfs) if k["t"] <= p + 1e-3), kfs[0])
            out.append((p, q, kf))
    return out


def base_key(edl: dict) -> str:
    src = Path(edl["source"]["path"])
    stat = f"{src.stat().st_size}:{int(src.stat().st_mtime)}" if src.exists() else "missing"
    blob = json.dumps({"src": str(src), "stat": stat, "pieces": [(a, b, k.get("layout"), k.get("cam"), k.get("path"),
                       k.get("t")) for a, b, k in pieces(edl)]}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def render_base(edl: dict, cache: Path, quality: str = "export") -> tuple[Path, dict]:
    cache.mkdir(parents=True, exist_ok=True)
    key = base_key(edl) + ("p" if quality == "proxy" else "")
    out = cache / f"base_{key}.mp4"
    meta_p = out.with_suffix(".json")
    if out.exists() and meta_p.exists():
        return out, json.loads(meta_p.read_text(encoding="utf-8"))
    src = edl["source"]
    ps = pieces(edl)
    if not ps:
        raise ValueError("Nothing left to render: every part of the clip was cut.")
    ss = max(0.0, ps[0][0] - 1.0)
    end = ps[-1][1]
    g, pads = [], []
    for i, (a, b, kf) in enumerate(ps):
        rel = [[float(t) - (a - kf["t"]), x] for t, x in (kf.get("path") or [[0, 0.5]])]
        rel = [[max(0.0, t), x] for t, x in rel] or [[0, 0.5]]
        g.append(f"[0:v]trim=start={a - ss:.3f}:end={b - ss:.3f},setpts=PTS-STARTPTS[s{i}]")
        g.append(piece_graph(kf["layout"], f"s{i}", f"v{i}", src["width"], src["height"], kf.get("cam"), rel))
        pads.append(f"[v{i}]")
        if src.get("has_audio", True):
            g.append(f"[0:a]atrim=start={a - ss:.3f}:end={b - ss:.3f},asetpts=PTS-STARTPTS[a{i}]")
            pads.append(f"[a{i}]")
    has_a = src.get("has_audio", True)
    g.append(f"{''.join(pads)}concat=n={len(ps)}:v=1:a={1 if has_a else 0}[vc]{'[ac]' if has_a else ''}")
    g.append("[vc]fps=30,format=yuv420p[vb]")
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{ss:.3f}", "-t", f"{end - ss + 0.5:.3f}", "-i", src["path"],
           "-filter_complex", ";".join(g), "-map", "[vb]"]
    if has_a:
        cmd += ["-map", "[ac]", "-c:a", "aac", "-b:a", "256k", "-ar", "48000"]
    codec = "libx264" if quality == "proxy" else pick_encoder("auto")
    vargs = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "26"] if quality == "proxy" else encoder_args(codec, "high")
    res = proc.run(cmd + [*vargs, "-pix_fmt", "yuv420p", str(out)], capture_output=True, text=True)
    if res.returncode != 0 and codec != "libx264":
        res = proc.run(cmd + [*encoder_args("libx264", "high"), "-pix_fmt", "yuv420p", str(out)],
                             capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg failed (edit base): {res.stderr[-1500:]}")
    first = ps[0][2]["layout"]
    meta = {"layout": "crop" if first in ("speaker", "pip") else first, "src_w": src["width"], "src_h": src["height"],
            "has_audio": has_a, "duration": round(sum(b - a for a, b, _ in ps), 3), "key": key}
    meta_p.write_text(json.dumps(meta), encoding="utf-8")
    return out, meta


def licensed(path: str) -> bool:
    """Only tracks in the music library (with a recorded licence, enabled) can be put in a video."""
    from .. import db
    r = db.q1("SELECT licence FROM music_tracks WHERE path=? AND enabled=1", (str(path),))
    return bool(r and r["licence"])


def layout_segments(edl: dict) -> list[tuple[float, str]]:
    segs, acc, last = [], 0.0, None
    for a, b, kf in pieces(edl):
        if kf["layout"] != last:
            segs.append((round(acc, 3), kf["layout"]))
            last = kf["layout"]
        acc += b - a
    return segs


def broll_licensed(path: str | Path) -> bool:
    """B-roll only from your own library folder (Settings › Music & B-roll); nothing is downloaded."""
    if not path:
        return False
    p = Path(path).resolve()
    lib = (db.get_setting("music", {}) or {}).get("broll_dir")
    return bool(lib) and (Path(lib).resolve() == p.parent or Path(lib).resolve() in p.parents) and p.exists()


def spec_for(edl: dict, out: Path, base: Path, quality: str = "export", music: bool = True,
             template: dict | None = None) -> RenderSpec:
    dur = T.duration(edl)
    words = T.words_out(edl)
    cap = edl["captions"]
    if cap.get("translate_to") and cap.get("translations", {}).get(cap["translate_to"]):
        words = T.words_out(edl, cap["translations"][cap["translate_to"]])
    m = edl["music"]
    use_music = music and m.get("enabled", True) and m.get("path") and not edl["rules"].get("no_music") \
        and licensed(m["path"])
    zooms = []
    for z in edl.get("zooms", []):
        t = T.to_out(edl, float(z["t"]))
        if t is not None:
            zooms.append({**z, "t": t})
    fl = T.intervals_out(edl, [(o["t"], o["t"] + o.get("dur", 0.15)) for o in edl.get("overlays", []) if o["kind"] == "flash"])
    sh = T.intervals_out(edl, [(o["t"], o["t"] + o.get("dur", 0.4)) for o in edl.get("overlays", []) if o["kind"] == "shake"])
    from ..music import sfx as sfxlib
    sfx = []
    for fx in edl.get("sfx", []):
        t = T.to_out(edl, float(fx["t"]))
        if t is not None and fx.get("path") and sfxlib.licensed(fx["path"]):     # bundled pack or your own folder
            sfx.append({**fx, "t": t, "gain_db": float(fx.get("gain_db", -8))})
    emojis = []
    for o in edl.get("overlays", []):
        t = T.to_out(edl, float(o["t"])) if o.get("kind") == "emoji" else None
        if t is not None and o.get("emoji"):
            emojis.append({"emoji": o["emoji"], "t": t, "dur": float(o.get("dur", 1.2)), "size": o.get("size", 220),
                           "x": o.get("x", 540), "y": o.get("y", 560)})
    broll = []
    for b in edl.get("broll", []):
        t = T.to_out(edl, float(b["t"]))
        if t is not None and broll_licensed(b.get("path", "")):
            broll.append({"path": b["path"], "t": t, "dur": float(b.get("dur", 2.0))})
    brand = edl.get("brand") or {}
    dur_out = T.duration(edl)
    cards = []
    if brand.get("intro_text"):
        cards.append({"text": brand["intro_text"], "start": 0.0, "end": min(0.8, dur_out / 4),
                      "bg": brand.get("card_bg"), "fg": brand.get("card_fg")})
    if brand.get("outro_text"):
        cards.append({"text": brand["outro_text"], "start": max(0.0, dur_out - 1.6), "end": dur_out,
                      "bg": brand.get("card_bg"), "fg": brand.get("card_fg")})
    logo = {"path": brand["logo_path"], "pos": brand.get("logo_pos", "tr"), "opacity": brand.get("logo_opacity", 0.85),
            "size": brand.get("logo_size", 150)} if brand.get("logo_path") else None
    speech = [(w["start"], w["end"]) for w in T.words_out(edl)]
    peak = edl["moment"].get("peak")
    peak_out = T.to_out(edl, peak) if peak is not None else None
    swells = [(max(0.0, peak_out - 0.3), min(dur, peak_out + 2.0))] if peak_out is not None else []
    rules = edl["rules"]
    return RenderSpec(
        source=base, start=0.0, end=dur, out=out, layout="crop", words=words, base=base,
        template=template, caption_style=cap.get("template") if isinstance(cap.get("template"), str) else "bold_pop",
        captions_enabled=cap.get("enabled", True) and edl["export"].get("captions", "burned") == "burned",
        caption_nudge=float(cap.get("nudge", 0)), keep_clear=tuple(rules.get("onscreen") or ()),
        hook=edl["hook"].get("text"), hook_sec=float(edl["hook"].get("sec", 2.2)),
        credit=None if edl.get("credit_hidden") else rules.get("credit"),
        progress_bar=edl.get("progress_bar", True), punch_zoom=False,
        layout_segments=layout_segments(edl), zooms=zooms, flashes=fl, shakes=sh, sfx=sfx,
        music=Path(m["path"]) if use_music else None, music_offset=float(m.get("offset", 0)),
        music_entry=float(m.get("entry", 0)), music_delay=float(m.get("delay", 0)), music_track_lufs=m.get("loudness"),
        music_volume_db=float(m.get("volume_db", 0)), duck_db=float(m.get("duck_db", 15)), speech=speech, swells=swells,
        beats=[b for b in (m.get("zoom_beats") or []) if b <= dur] if use_music else [],
        speech_gain_db=float(edl["audio"].get("speech_gain_db", 0)), enhance=bool(edl["audio"].get("enhance")),
        denoise=bool(edl["audio"].get("denoise")), target_lufs=float(edl["audio"].get("target_lufs", -14)),
        scale_to=(540, 960) if quality == "proxy" else None, quality="fast" if quality == "proxy" else "high",
        encoder="libx264" if quality == "proxy" else "auto",
        emojis=emojis, broll=broll, logo=logo, cards=cards,
        progress_color=brand.get("progress_color") or "FFE500",
        credit_pos=brand.get("credit_pos"),
        )


def render(edl: dict, out: Path, cache: Path, quality: str = "export", music: bool = True,
           template: dict | None = None, preset: str | None = None) -> dict:
    """Render the edit project. preset: export preset key (bitrate, length check, burned captions / .srt)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    p = PRESETS.get(preset or edl["export"].get("preset") or "tiktok", PRESETS["tiktok"])
    if T.duration(edl) > p["max_sec"]:
        raise ValueError(f"{p['label']} allows up to {p['max_sec']} s; this clip is {T.duration(edl):.0f} s. Trim it first.")
    e = json.loads(json.dumps(edl))
    e["export"] = {**e.get("export", {}), "captions": p["captions"] if quality == "export" else "burned"}
    base, meta = render_base(e, cache, quality)
    spec = spec_for(e, out, base, quality, music, template)
    if quality == "export":
        spec.max_bitrate = p["bitrate"]
        if p["captions"] == "srt":
            spec.srt_out = out.with_suffix(".srt")
    res = finish(spec, base, meta, out=out, music=music)
    res.update({"base": str(base), "preset": preset, "srt": str(spec.srt_out) if spec.srt_out else None})
    return res
