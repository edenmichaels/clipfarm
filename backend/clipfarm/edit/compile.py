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
def _rounded(out: str, pw: int, ph: int) -> str:
    """The pad's picture with a thin white border, rounded corners (alpha) -> [out]."""
    from .facecam import BORDER, RADIUS
    r = RADIUS
    return (f"pad=iw+{2 * BORDER}:ih+{2 * BORDER}:{BORDER}:{BORDER}:color=white,format=yuva420p,"
            f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':"
            f"a='255*lte(hypot(max(0,abs(X-W/2+0.5)-(W/2-{r})),max(0,abs(Y-H/2+0.5)-(H/2-{r}))),{r})'[{out}]")


def _pip_over(base: str, small: str, out: str, rect: tuple[int, int, int, int]) -> str:
    """Soft shadow, then the rounded picture, over the base picture at rect (border included)."""
    x, y, pw, ph = rect
    sp = 26
    return (f"color=c=black:s={pw}x{ph}:r=30,format=yuva420p,colorchannelmixer=aa=0.5,"
            f"pad={pw + 2 * sp}:{ph + 2 * sp}:{sp}:{sp}:color=black@0,boxblur=luma_radius=0:chroma_radius=0:"
            f"alpha_radius=12:alpha_power=2[{out}sh];"
            f"[{base}][{out}sh]overlay=x={x - sp + 4}:y={y - sp + 9}:shortest=1[{out}bs];"
            f"[{out}bs][{small}]overlay=x={x}:y={y}:shortest=1,setsar=1[{out}]")


def piece_graph(lay: str, inp: str, out: str, sw: int, sh: int, cam: dict | None, path: list,
                cam_h: int = 640, seam_line: bool = False, pip: dict | None = None, has_title: bool = False) -> str:
    """One shot -> 1080x1920. Every crop here is FIXED for the whole shot: nothing pans, slides or zooms."""
    keys = [(float(t), float(x)) for t, x in (path or [[0, 0.5]])]
    if lay in ("corner", "focus") and cam:
        from . import facecam as F
        fx, fy, fw, fh = int(cam["x"] * sw), int(cam["y"] * sh), int(cam["w"] * sw), int(cam["h"] * sh)
        fw, fh = max(2, fw // 2 * 2), max(2, fh // 2 * 2)
        cw = min(sw, int(sh * 9 / 16)) // 2 * 2
        gx = L.fixed_x(keys, sw, cw)
        if lay == "corner":
            x, y, pw, ph = F.pip_rect(pip, F.cam_aspect(cam, sw, sh), has_title)
            iw, ih = pw - 2 * F.BORDER, ph - 2 * F.BORDER
            return (f"[{inp}]split=2[{out}a][{out}b];"
                    f"[{out}a]crop={cw}:{sh}:{gx}:0,scale={W}:{H},setsar=1[{out}g];"
                    f"[{out}b]crop={fw}:{fh}:{fx}:{fy},scale={iw}:{ih}:force_original_aspect_ratio=increase,"
                    f"crop={iw}:{ih},setsar=1,{_rounded(out + 'c', iw, ih)};"
                    + _pip_over(f"{out}g", f"{out}c", out, (x, y, pw, ph)))
        # focus: the streamer fills the frame (fixed crop of the webcam), the gameplay small in the corner
        x, y, pw, ph = F.pip_rect({**(pip or {}), "w": max(0.38, float((pip or {}).get("w", 0.4)))}, sw / sh, has_title)
        iw, ih = pw - 2 * F.BORDER, ph - 2 * F.BORDER
        return (f"[{inp}]split=2[{out}a][{out}b];"
                f"[{out}a]crop={fw}:{fh}:{fx}:{fy},scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1[{out}f];"
                f"[{out}b]scale={iw}:{ih}:force_original_aspect_ratio=increase,crop={iw}:{ih},setsar=1,"
                f"{_rounded(out + 'c', iw, ih)};"
                + _pip_over(f"{out}f", f"{out}c", out, (x, y, pw, ph)))
    if lay in ("corner", "focus"):
        lay = "crop"
    if lay == "cam_full" and cam:
        # Full Reaction: the facecam fills the screen (scaled up to cover 1080x1920, centred on the cam box)
        fx, fy, fw, fh = int(cam["x"] * sw), int(cam["y"] * sh), int(cam["w"] * sw), int(cam["h"] * sh)
        return (f"[{inp}]crop={max(2, fw // 2 * 2)}:{max(2, fh // 2 * 2)}:{fx}:{fy},"
                f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[{out}]")
    if lay == "cam_full":
        lay = "crop"
    if lay == "split" and cam:
        cam_h = max(560, min(900, int(cam_h) // 2 * 2))
        game_h = H - cam_h
        seam = f",drawbox=x=0:y={cam_h - 3}:w={W}:h=6:color=black@0.85:t=fill" if seam_line else ""
        fx, fy, fw, fh = int(cam["x"] * sw), int(cam["y"] * sh), int(cam["w"] * sw), int(cam["h"] * sh)
        gw = min(sw, int(sh * W / game_h)) // 2 * 2
        return (f"[{inp}]split=2[{out}a][{out}b];"
                f"[{out}a]crop={fw}:{fh}:{fx}:{fy},scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[{out}c];"
                f"[{out}b]crop={gw}:{sh}:{L.fixed_x(keys, sw, gw)}:0,scale={W}:{game_h}:force_original_aspect_ratio=increase,"
                f"crop={W}:{game_h}[{out}g];[{out}c][{out}g]vstack=inputs=2,setsar=1{seam}[{out}]")
    if lay in ("crop", "speaker") or (lay == "pip" and not cam):
        cw = min(sw, int(sh * 9 / 16)) // 2 * 2
        return f"[{inp}]crop={cw}:{sh}:{L.fixed_x(keys, sw, cw)}:0,scale={W}:{H},setsar=1[{out}]"
    if lay == "pip":
        cw = min(sw, int(sh * 9 / 16)) // 2 * 2
        fx, fy, fw, fh = int(cam["x"] * sw), int(cam["y"] * sh), int(cam["w"] * sw), int(cam["h"] * sh)
        return (f"[{inp}]split=2[{out}a][{out}b];"
                f"[{out}a]crop={cw}:{sh}:{L.fixed_x(keys, sw, cw)}:0,scale={W}:{H},setsar=1[{out}g];"
                f"[{out}b]crop={fw}:{fh}:{fx}:{fy},scale=420:-2,pad=iw+8:ih+8:4:4:white[{out}c];"
                f"[{out}g][{out}c]overlay=x=60:y=250[{out}]")
    return (f"[{inp}]split=2[{out}a][{out}b];"
            f"[{out}a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2,eq=brightness=-0.08[{out}bg];"
            f"[{out}b]scale={W}:-2[{out}fg];[{out}bg][{out}fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")


def _has_title(edl: dict) -> bool:
    """A title at the top pushes the corner facecam down (it never covers the title)."""
    h = edl.get("hook") or {}
    return bool(h.get("text")) and not h.get("hidden") and h.get("box") != "none"


def _item(edl: dict, i: int) -> dict:
    """Item 0 = the main clip; 1.. = appended sequence clips (each has its own source, range, cuts, layout)."""
    if i == 0:
        return {"source": edl["source"], "layout": edl["layout"]}
    it = edl["sequence"][i - 1]
    return {"source": it["source"], "layout": it.get("layout") or {"keyframes": []}}


def pieces(edl: dict) -> list[tuple[T.Seg, dict]]:
    """Output segments (speed ramps, freezes, sequence items) split at layout keyframes: [(segment, keyframe)]."""
    out = []
    for sg in T.segments(edl):
        it = _item(edl, sg.item)
        kfs = sorted(it["layout"].get("keyframes") or [], key=lambda k: k["t"]) or \
            [{"t": sg.a, "layout": "blur_fill", "cam": None, "path": [[0, 0.5]]}]
        if sg.hold:
            kf = next((k for k in reversed(kfs) if k["t"] <= sg.a + 1e-3), kfs[0])
            out.append((sg, kf))
            continue
        cuts = sorted({sg.a, sg.b, *[k["t"] for k in kfs if sg.a < k["t"] < sg.b]})
        for p, q in zip(cuts, cuts[1:]):
            if q - p < T.MIN_SEG:
                continue
            kf = next((k for k in reversed(kfs) if k["t"] <= p + 1e-3), kfs[0])
            out.append((sg._replace(a=p, b=q), kf))
    return out


def base_key(edl: dict) -> str:
    srcs = [Path(edl["source"]["path"])] + [Path(it["source"]["path"]) for it in edl.get("sequence") or []]
    stat = [f"{p}:{p.stat().st_size}:{int(p.stat().st_mtime)}" if p.exists() else f"{p}:missing" for p in srcs]
    lay = edl.get("layout") or {}
    blob = json.dumps({"src": stat, "cam_h": lay.get("cam_h", 640), "seam": bool(lay.get("seam_line")),
                       "pip": lay.get("pip"), "title": _has_title(edl),
                       "pieces": [(tuple(sg), k.get("layout"), k.get("cam"), k.get("path"), k.get("t"))
                                  for sg, k in pieces(edl)]}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _atempo(speed: float) -> str:
    """atempo takes 0.5-2 per stage; chain stages for 0.25-4."""
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


AFMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def render_base(edl: dict, cache: Path, quality: str = "export") -> tuple[Path, dict]:
    cache.mkdir(parents=True, exist_ok=True)
    key = base_key(edl) + ("p" if quality == "proxy" else "")
    out = cache / f"base_{key}.mp4"
    meta_p = out.with_suffix(".json")
    if out.exists() and meta_p.exists():
        return out, json.loads(meta_p.read_text(encoding="utf-8"))
    ps = pieces(edl)
    if not ps:
        raise ValueError("Nothing left to render: every part of the clip was cut.")
    # one input per item, each seeked close to the part it uses
    items = sorted({sg.item for sg, _ in ps})
    inputs, idx, ss_of = [], {}, {}
    for i in items:
        src = _item(edl, i)["source"]
        used = [sg for sg, _ in ps if sg.item == i]
        ss = max(0.0, min(sg.a for sg in used) - 1.0)
        end = max(sg.b for sg in used) + 0.5
        idx[i], ss_of[i] = len(idx), ss
        inputs += ["-ss", f"{ss:.3f}", "-t", f"{end - ss:.3f}", "-i", src["path"]]
    main_src = edl["source"]
    has_a = main_src.get("has_audio", True) or any(_item(edl, i)["source"].get("has_audio", True) for i in items)
    cam_h, seam = int(edl["layout"].get("cam_h", 640)), bool(edl["layout"].get("seam_line"))
    pip, has_title = edl["layout"].get("pip"), _has_title(edl)
    g, pads = [], []
    for n, (sg, kf) in enumerate(ps):
        src, k, ss = _item(edl, sg.item)["source"], idx[sg.item], ss_of[sg.item]
        a = sg.a - ss
        if sg.hold:
            g.append(f"[{k}:v]trim=start={a:.3f}:end={a + 0.05:.3f},setpts=PTS-STARTPTS,"
                     f"tpad=stop_mode=clone:stop_duration={sg.hold:.3f},trim=duration={sg.hold:.3f}[s{n}]")
            rel = [[0, x] for _, x in (kf.get("path") or [[0, 0.5]])[:1]]
        else:
            sp = "" if sg.speed == 1 else f",setpts=PTS/{sg.speed:.4f}"
            g.append(f"[{k}:v]trim=start={a:.3f}:end={sg.b - ss:.3f},setpts=PTS-STARTPTS{sp}[s{n}]")
            rel = [[(float(t) - (sg.a - kf["t"])) / sg.speed, x] for t, x in (kf.get("path") or [[0, 0.5]])]
            rel = [[max(0.0, t), x] for t, x in rel] or [[0, 0.5]]
        g.append(piece_graph(kf["layout"], f"s{n}", f"v{n}", src["width"], src["height"], kf.get("cam"), rel,
                             cam_h, seam, pip, has_title))
        g.append(f"[v{n}]fps=30,format=yuv420p[w{n}]")
        pads.append(f"[w{n}]")
        if has_a:
            if sg.hold or not src.get("has_audio", True):
                ln = sg.length
                g.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={ln:.3f},{AFMT}[a{n}]")
            else:
                tempo = "" if sg.speed == 1 else "," + _atempo(sg.speed)
                g.append(f"[{k}:a]atrim=start={a:.3f}:end={sg.b - ss:.3f},asetpts=PTS-STARTPTS{tempo},{AFMT}[a{n}]")
            pads.append(f"[a{n}]")
    g.append(f"{''.join(pads)}concat=n={len(ps)}:v=1:a={1 if has_a else 0}[vc]{'[ac]' if has_a else ''}")
    g.append("[vc]fps=30,format=yuv420p[vb]")
    cmd = [ffmpeg_bin(), "-y", "-v", "error", *inputs, "-filter_complex", ";".join(g), "-map", "[vb]"]
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
    first = ps[0][1]["layout"]
    meta = {"layout": "crop" if first in ("speaker", "pip", "cam_full", "corner", "focus") else first, "src_w": main_src["width"],
            "src_h": main_src["height"], "has_audio": has_a, "duration": round(sum(sg.length for sg, _ in ps), 3),
            "key": key}
    meta_p.write_text(json.dumps(meta), encoding="utf-8")
    return out, meta


def licensed(path: str) -> bool:
    """Only tracks in the music library (with a recorded licence, enabled) can be put in a video."""
    from .. import db
    r = db.q1("SELECT licence FROM music_tracks WHERE path=? AND enabled=1", (str(path),))
    return bool(r and r["licence"])


def layout_segments(edl: dict) -> list[tuple[float, str]]:
    segs, acc, last = [], 0.0, None
    for sg, kf in pieces(edl):
        if kf["layout"] != last:
            segs.append((round(acc, 3), kf["layout"]))
            last = kf["layout"]
        acc += sg.length
    return segs


def broll_licensed(path: str | Path) -> bool:
    """B-roll only from your own library folder (Settings › Music & B-roll); nothing is downloaded."""
    if not path:
        return False
    p = Path(path).resolve()
    lib = (db.get_setting("music", {}) or {}).get("broll_dir")
    return bool(lib) and (Path(lib).resolve() == p.parent or Path(lib).resolve() in p.parents) and p.exists()


def sticker_licensed(path: str | Path) -> bool:
    """Image stickers only from your own sticker folder (Settings › Music & B-roll); nothing is downloaded."""
    if not path:
        return False
    p = Path(path).resolve()
    lib = (db.get_setting("music", {}) or {}).get("stickers_dir")
    return bool(lib) and Path(lib).resolve() in p.parents and p.exists()


def layer_specs(edl: dict, dur: float) -> dict:
    """The Editor's free layers (output time): text, stickers, colour, crop/rotate, transitions, audio keyframes."""
    def clamp_t(x):
        return max(0.0, min(dur, float(x)))
    texts = [{**t, "start": clamp_t(t.get("start", 0)), "end": clamp_t(t.get("end", dur))}
             for t in edl.get("text_layers") or [] if not t.get("hidden")]
    emojis, images = [], []
    for st in edl.get("stickers") or []:
        if st.get("hidden"):
            continue
        a, b = clamp_t(st.get("start", 0)), clamp_t(st.get("end", dur))
        if b <= a:
            continue
        if st.get("emoji"):
            emojis.append({"emoji": st["emoji"], "t": a, "dur": b - a, "size": int(st.get("size", 240)),
                           "x": float(st.get("x", 0.5)) * 1080, "y": float(st.get("y", 0.5)) * 1920,
                           "rotate": float(st.get("rotate", 0))})
        elif sticker_licensed(st.get("path", "")):
            images.append({**st, "start": a, "end": b})
    tr = [dict(x, t=clamp_t(x["t"])) for x in edl.get("transitions") or [] if x.get("kind") in ("fade", "flash", "zoom", "whip")]
    zooms = [{"t": max(0.0, x["t"] - float(x.get("dur", 0.4)) / 2), "kind": "punch",
              "amount": 0.25 if x["kind"] == "zoom" else 0.15, "dur": float(x.get("dur", 0.4))}
             for x in tr if x["kind"] in ("zoom", "whip")]
    shakes: list = []          # v0.8.5: a "whip" is an instant zoom cut now, never a shake
    return {"text_layers": texts, "emojis": emojis, "stickers": images, "color": edl.get("color"),
            "transform": edl.get("transform"), "transitions": tr, "zooms": zooms, "shakes": shakes,
            "audio_keys": edl.get("audio_keys") or {}}


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
    lay = layer_specs(edl, dur)
    emojis += lay["emojis"]
    zooms += lay["zooms"]
    sh = sorted([*sh, *lay["shakes"]])
    rules = edl["rules"]
    hk = edl["hook"]
    ov = cap.get("style_overrides") or {}
    if ov:
        from . import templates as _tm
        template = {**(template or _tm.get(cap.get("template") if isinstance(cap.get("template"), str) else None)), **ov}
    return RenderSpec(
        source=base, start=0.0, end=dur, out=out, layout="crop", words=words, base=base,
        template=template, caption_style=cap.get("template") if isinstance(cap.get("template"), str) else "bold_pop",
        captions_enabled=cap.get("enabled", True) and edl["export"].get("captions", "burned") == "burned",
        caption_nudge=float(cap.get("nudge", 0)),
        hook=None if hk.get("hidden") else hk.get("text"),
        hook_sec=dur if hk.get("whole") else float(hk.get("sec", 2.2)),
        title_box=hk.get("box") or "outline",
        words_per_chunk=cap.get("words_per_chunk"), emoji=bool(cap.get("emoji")),
        credit=None if edl.get("credit_hidden") else rules.get("credit"),
        progress_bar=edl.get("progress_bar", True), zooms=zooms, flashes=fl, shakes=sh, sfx=sfx,
        music=Path(m["path"]) if use_music else None, music_offset=float(m.get("offset", 0)),
        music_entry=float(m.get("entry", 0)), music_delay=float(m.get("delay", 0)), music_track_lufs=m.get("loudness"),
        music_volume_db=float(m.get("volume_db", 0)), duck_db=float(m.get("duck_db", 15)), speech=speech, swells=swells,
        speech_gain_db=float(edl["audio"].get("speech_gain_db", 0)), enhance=bool(edl["audio"].get("enhance")),
        denoise=bool(edl["audio"].get("denoise")), target_lufs=float(edl["audio"].get("target_lufs", -14)),
        scale_to=(540, 960) if quality == "proxy" else None, quality="fast" if quality == "proxy" else "high",
        encoder="libx264" if quality == "proxy" else "auto",
        emojis=emojis, broll=broll, logo=logo, cards=cards,
        progress_color=brand.get("progress_color") or "FFE500",
        text_layers=lay["text_layers"], stickers=lay["stickers"], color=lay["color"], transform=lay["transform"],
        transitions=lay["transitions"], audio_keys=lay["audio_keys"], credit_pos=brand.get("credit_pos"),
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


def render_frame(edl: dict, t_out: float, out: Path, cache: Path, quality: str = "proxy",
                 template: dict | None = None) -> dict:
    """One still frame of the edit at output time t, through the exact export chain (same zoom, same libass
    captions, same overlays). The Editor shows this while the playable preview is being rebuilt."""
    out.parent.mkdir(parents=True, exist_ok=True)
    e = json.loads(json.dumps(edl))
    e["export"] = {**e.get("export", {}), "captions": "burned"}
    base, meta = render_base(e, cache, quality)
    spec = spec_for(e, out, base, quality, True, template)
    spec.scale_to = None                      # stills are compared with the export at full 1080x1920
    return finish(spec, base, meta, out=out, frame_at=t_out)


def caption_y(edl: dict, template: dict | None = None) -> float | None:
    """Middle of a one-line caption (1920 px canvas, before your nudge), for the Editor's drag handle. Captions sit in
    one fixed place for every clip and layout, so this is a constant (None when the clip has no captions)."""
    from ..editing import captions as C
    if not edl["captions"].get("enabled", True) or not T.words_out(edl):
        return None
    pr = C.template_from(template or edl["captions"].get("template"))
    return round(C.BASELINE - pr.em * 0.35, 1)
