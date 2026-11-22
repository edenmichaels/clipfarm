"""Before/after sample clips from the same source (CI). Run once inside the v0.3 checkout and once in v0.4.

Usage (from <checkout>/backend): python <this> <eval dir> <out dir> <label: before|after> <music track file>
  captions.mp4  the same funny moment, each version's captions (no music)
  moment.mp4    each version's own top pick from the same VOD (no music)
  music.mp4     the same action moment with music: v0.3 = one fixed track mixed at a constant level;
                v0.4 = track picked for the moment type, dropped on the payoff, ducked under speech
  reframe.mp4   the same moment: v0.3 = centre crop; new = AI reframe (scene type, facecam, tracking keyframes)
  full_edit.mp4 the same moment fully auto-edited: new = reframe + matched music + auto zooms/effects/SFX
Words come from faster-whisper (tiny) so both versions caption the same transcript.
"""
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from clipfarm.editing import render as R  # noqa: E402
from clipfarm.highlights import signals  # noqa: E402

ev_dir, out, label, music = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3], Path(sys.argv[4]).resolve()
out.mkdir(parents=True, exist_ok=True)
vod = ev_dir / "vod1.mp4"
labels = json.loads((ev_dir / "labels.json").read_text())["vod1"]
funny = next(l for l in labels if l["type"] == "Funny")
action = next(l for l in labels if l["type"].startswith("Action"))
chat = [(m["t"], m["m"]) for m in map(json.loads, (ev_dir / "chat1.jsonl").read_text().splitlines())]
NEW = hasattr(R, "render_base")

from faster_whisper import WhisperModel  # noqa: E402
wm = WhisperModel("tiny", device="cpu", compute_type="int8")


def words(a, b):
    # Decode with ffmpeg ourselves (faster-whisper's PyAV call can clash with the installed PyAV version).
    import subprocess

    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(a), "-t", str(b - a), "-i", str(vod), "-ac", "1",
                          "-ar", "16000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    segs, _ = wm.transcribe(np.frombuffer(raw, np.float32), word_timestamps=True)
    return [{"word": w.word.strip(), "start": a + float(w.start), "end": a + float(w.end)}
            for s in segs for w in s.words or []]


def spec(start, end, name, **kw):
    fields = {f.name for f in dataclasses.fields(R.RenderSpec)}
    base = dict(source=vod, start=start, end=end, out=out / name, layout="blur_fill", quality="high",
                hook=None, credit="twitch.tv/samplestreamer")
    base.update(kw)
    return R.RenderSpec(**{k: v for k, v in base.items() if k in fields})


def clean():
    for p in out.glob("*"):
        if p.suffix not in (".mp4",) or ".base." in p.name:
            p.unlink(missing_ok=True)


a, b = funny["start"] - 4, funny["end"] + 2
R.render(spec(a, b, "captions.mp4", words=words(a, b)))

# moment: each version's own top pick
if NEW:
    from clipfarm.highlights import run, visual
    vis = visual.analyse(vod)
    ms, _ = run.detect_source(vod, chat=chat, budget=1, threshold=0.0,
                              visual_fn=lambda x, y: {k: v[int(x):int(y)] for k, v in vis.items()})
    m = ms[0]
    pick = {"start": m.start, "end": m.end, "type": m.type, "reason": m.reason}
else:
    from clipfarm.highlights import scorer
    audio = signals.audio_rms(vod)
    rate, burst = signals.chat_series(chat, len(audio))
    curve, normed = scorer.composite({"audio": audio, "chat": rate, "emote": burst})
    m = max(scorer.pick_moments(curve, normed, threshold=0.0, top_n_per_hour=60), key=lambda x: x.score)
    pick = {"start": m.start, "end": m.end, "type": None, "reason": "loudness + chat"}
R.render(spec(pick["start"], pick["end"], "moment.mp4", words=words(pick["start"], pick["end"])))

# music
a, b = action["start"] - 3, action["end"] + 2
ws = words(a, b)
if NEW:
    from clipfarm import db
    from clipfarm.music import library, pick as mp
    db.init_db()
    library.ensure_bundled()
    tr = mp.choose("Action/Explosion", [1], seed=3)
    payoff = action["start"] + 4 - a                  # first explosion in the labelled moment
    pl = mp.plan(tr, "Action/Explosion", payoff, b - a)
    R.render(spec(a, b, "music.mp4", words=ws, music=Path(tr["path"]), music_offset=pl["offset"],
                  music_entry=pl["entry"], music_delay=pl["delay"], music_track_lufs=tr["loudness"],
                  speech=[(w["start"] - a, w["end"] - a) for w in ws], swells=[(payoff - 0.3, payoff + 2)],
                  beats=pl["zoom_beats"]))
    music_note = f"{tr['title']} ({', '.join(tr['moods'])}), {pl['mode']}, ducked under speech"
else:
    R.render(spec(a, b, "music.mp4", words=ws, music=music, music_volume=0.12))
    music_note = f"{music.name} at a constant level (v0.3 behaviour)"
# reframe + full auto-edit (v0.5+ edit engine; older versions use their default layout and no effects)
try:
    from clipfarm.edit import compile as ec, effects as fx, project as ep, reframe  # noqa: E402
    EDL = True
except ImportError:
    EDL = False
a, b = funny["start"] - 4, funny["end"] + 2
ws = words(a, b)
if EDL:
    from clipfarm import db
    db.init_db()
    from clipfarm.edit import build as eb
    e = ep.blank({**eb.source_info(vod), "url": None}, ws, a, b)
    lay = reframe.analyse(vod, a, b, speech=[(w["start"], w["end"]) for w in ws], detect_cam=True)
    e["layout"].update(lay)
    e["music"]["enabled"] = False
    e["hook"]["text"] = "Wait for it"
    ec.render(e, out / "reframe.mp4", out / "cache", "export")
    e["moment"].update(type="Funny", peak=funny["start"] + 2)
    try:
        from clipfarm.music import library
        library.ensure_bundled()
        e = eb.plan_music(e, [1], {"enabled": True}, None, seed=3)
    except Exception as ex:      # noqa: BLE001
        print("music plan skipped:", ex)
    e = fx.auto_effects(e) if hasattr(fx, "auto_effects") else e
    ec.render(e, out / "full_edit.mp4", out / "cache", "export")
    reframe_note = {"layouts": [k["layout"] for k in e["layout"]["keyframes"]],
                    "effects": {k: len(e.get(k, [])) for k in ("zooms", "overlays", "sfx")}}
else:
    R.render(spec(a, b, "reframe.mp4", words=ws, layout="crop"))
    R.render(spec(a, b, "full_edit.mp4", words=ws, music=music, music_volume=0.12))
    reframe_note = {"layouts": ["crop (centre)"], "effects": {}}
import shutil  # noqa: E402
shutil.rmtree(out / "cache", ignore_errors=True)
clean()
(out / "NOTES.json").write_text(json.dumps({"label": label, "captions_window": [funny["start"] - 4, funny["end"] + 2],
                                            "moment_pick": pick, "music": music_note, "reframe": reframe_note}, indent=1))
print("samples ready", out)
