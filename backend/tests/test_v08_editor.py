"""v0.8.3 Editor engine: speed ramps, freeze frames, multi-clip sequences, text layers, stickers, colour,
crop/rotate, transitions and audio keyframes -- in the timeline maths and in real renders (preview == export)."""
from __future__ import annotations

import json
import subprocess

import numpy as np

from clipfarm.edit import compile as ec, timeline as T
from clipfarm.editing.ffmpeg_util import probe
from test_v08_bugs import _edl, _frames, _grey_source, _png


def _bars(path, dur=12):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={dur}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={dur}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(path)], check=True)


def test_timeline_speed_freeze_and_sequence_maths():
    e = {"range": [0.0, 10.0], "cuts": [[4.0, 5.0]], "speed": [{"a": 6.0, "b": 8.0, "speed": 2.0}],
         "freezes": [{"t": 2.0, "dur": 1.5}], "captions": {"words": []},
         "sequence": [{"range": [3.0, 6.0], "cuts": [], "words": [{"word": "hi", "start": 3.5, "end": 3.8}]}]}
    # main: 0-4 (4s) + freeze 1.5 + 5-6 (1) + 6-8 at 2x (1) + 8-10 (2) = 9.5 ; sequence 3s
    assert T.duration(e) == 12.5
    assert T.to_out(e, 1.0) == 1.0 and T.to_out(e, 3.0) == 4.5          # after the freeze
    assert T.to_out(e, 4.5) is None                                     # cut
    assert T.to_out(e, 7.0) == 7.0                                      # 5.5 + 1 + (7-6)/2
    assert T.to_out(e, 3.5, item=1) == 10.0                             # sequence item starts at 9.5
    assert T.item_offset(e, 1) == 9.5
    assert [w["start"] for w in T.words_out(e)] == [10.0]
    assert T.to_src(e, 7.0) == 7.0 and T.to_src(e, 3.0) == 2.0           # inside the freeze -> the held frame
    assert T.intervals_out(e, [(6.0, 8.0)]) == [(6.5, 7.5)]


def test_speed_ramp_freeze_and_sequence_render(fresh_db, tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _bars(a)
    _grey_source(b, 8)
    e = _edl(a, 1.0, 9.0, "crop", "bold_pop")
    e["source"].update(path=str(a))
    e["speed"] = [{"a": 3.0, "b": 7.0, "speed": 2.0}]
    e["freezes"] = [{"t": 2.0, "dur": 1.0}]
    e["sequence"] = [{"source": {"path": str(b), "width": 1920, "height": 1080, "has_audio": True},
                      "range": [1.0, 4.0], "cuts": [], "words": [],
                      "layout": {"keyframes": [{"t": 1.0, "layout": "blur_fill", "path": [[0, 0.5]]}]}}]
    want = T.duration(e)
    assert want == 8.0 - 2.0 + 1.0 + 3.0                                  # 8 s, 4 s of it at 2x, +1 freeze, +3
    res = ec.render(e, tmp_path / "o.mp4", tmp_path / "cache", "export")
    assert abs(probe(res["path"])["duration"] - want) < 0.25
    f1, f2 = _frames(res["path"], [1.2, 1.8])                              # inside the freeze: the same frame
    assert np.abs(f1 - f2).mean() < 2.0
    tail = _frames(res["path"], [want - 1.0])[0]                          # the appended grey clip
    patch = tail[1700:1800, 300:700]
    assert 40 < patch.mean() < 95 and patch.std() < 6                      # flat grey, not the colour bars


def test_layers_render_where_they_should(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _grey_source(src, 8)
    e = _edl(src, 1.0, 7.0, "crop", "bold_pop")
    e["captions"]["enabled"] = False
    e["hook"]["text"] = None
    e["progress_bar"] = False
    e["text_layers"] = [{"id": "t1", "text": "TOP TEXT", "start": 0.0, "end": 6.0, "x": 0.5, "y": 0.8, "size": 90,
                         "box": "white_box"}]
    e["stickers"] = [{"id": "s1", "emoji": "🔥", "start": 1.0, "end": 3.0, "x": 0.25, "y": 0.25, "size": 260}]
    e["transitions"] = [{"t": 4.0, "kind": "fade", "dur": 0.6}]
    still = lambda t, name: _png(ec.render_frame(e, t, tmp_path / name, tmp_path / "cache")["path"] and tmp_path / name)  # noqa: E731
    f = still(2.0, "a.png")
    box = f[1536 - 40:1536 + 40, 300:780]
    assert (box.min(axis=2) > 235).mean() > 0.3, "white text box missing at y=80%"
    stk = f[480 - 100:480 + 100, 270 - 100:270 + 100]
    assert (stk[..., 0] - stk[..., 2] > 60).mean() > 0.15, "fire sticker missing"
    assert np.abs(f[1100:1200, 500:600] - 0x50).mean() < 12                 # nothing else on the picture
    dip = still(4.0, "b.png")
    assert dip[900:1000, 100:500].mean() < 20, "fade transition should dip to black"
    later = still(5.0, "c.png")
    assert (later[480 - 100:480 + 100, 170:370][..., 0] - later[480 - 100:480 + 100, 170:370][..., 2] > 60).mean() < 0.02
    # colour: saturation 0 makes the blue-ish facecam grey; crop/rotate keeps the frame filled
    e["color"] = {"saturation": 0.0}
    e["transform"] = {"rotate": 8, "zoom": 1.1}
    g = still(2.0, "d.png")
    assert np.abs(g[..., 2] - g[..., 0])[700:1400].mean() < 3     # (the sticker above stays colourful)
    assert g[:40, :40].mean() > 20 and g[-40:, -40:].mean() > 20          # no black corners after rotating
    # preview still == export frame
    res = ec.render(e, tmp_path / "o.mp4", tmp_path / "cache", "export")
    assert np.abs(_frames(res["path"], [2.0])[0] - g).mean() < 3.0


def test_audio_keyframes_mute_and_restore(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _grey_source(src, 8)
    e = _edl(src, 1.0, 7.0, "crop", "bold_pop")
    e["audio_keys"] = {"speech": [{"t": 1.0, "db": 0}, {"t": 1.2, "db": -60}, {"t": 3.0, "db": -60},
                                  {"t": 3.2, "db": 0}]}
    res = ec.render(e, tmp_path / "o.mp4", tmp_path / "cache", "export")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", res["path"], "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    pcm = np.frombuffer(raw, np.int16).astype(float)
    rms = lambda a, b: float(np.sqrt((pcm[int(a * 8000):int(b * 8000)] ** 2).mean()))  # noqa: E731
    assert rms(1.6, 2.6) < rms(4.0, 5.0) / 30


def test_view_exposes_segments(fresh_db):
    e = {"range": [0.0, 4.0], "cuts": [], "speed": [{"a": 1.0, "b": 2.0, "speed": 0.5}], "captions": {"words": []}}
    segs = T.out_segments(e)
    assert [(s["out_a"], s["out_b"], s["speed"]) for s in segs] == [(0.0, 1.0, 1.0), (1.0, 3.0, 0.5), (3.0, 5.0, 1.0)]
    json.dumps(segs)


def test_caption_handle_position_and_old_zoom_factors(fresh_db, tmp_path):
    from clipfarm.editing.render import RenderSpec, _zoom_expr
    src = tmp_path / "s.mp4"
    _grey_source(src, 8)
    e = _edl(src, 1.0, 7.0, "split", "bold_pop")
    y = ec.caption_y(e)
    assert y is not None and 640 < y < 900                   # on the seam of the split layout
    e["captions"]["nudge"] = 120
    assert ec.caption_y(e) == y                              # the handle adds the nudge itself
    # a zoom stored as a factor (1.16) used to zoom to 216 %; it's now read as +16 %
    old = _zoom_expr(RenderSpec(source=src, start=0, end=1, out=src, zooms=[{"t": 1.0, "amount": 1.16, "dur": 0.5}]))
    new = _zoom_expr(RenderSpec(source=src, start=0, end=1, out=src, zooms=[{"t": 1.0, "amount": 0.16, "dur": 0.5}]))
    assert old == new and "0.160*" in new
