"""v0.5 tests: edit projects (EDL), undo/redo, transcript editing, reframe, caption templates, export presets."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipfarm import db, migrations
from clipfarm.edit import compile as EC, project, reframe, templates, timeline as T, transcript as TR
from clipfarm.editing import captions as C

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
FREQS = [300, 500, 700, 900, 1100, 1300, 1500, 1700]


def _tone_source(path: Path, dur: float = 20.0) -> list[dict]:
    """A 1920x1080 video whose audio plays one tone per 'word' (0.4 s each, 0.1 s gaps) from t=4."""
    words, parts, t = [], [], 4.0
    for i, f in enumerate(FREQS):
        words.append({"word": f"w{i}", "start": round(t, 3), "end": round(t + 0.4, 3)})
        parts.append(f"sin(2*PI*{f}*t)*between(t,{t:.3f},{t + 0.4:.3f})")
        t += 0.5
    expr = "0.5*(" + "+".join(parts) + ")"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=30:d={dur}",
                    "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d={dur}", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "pcm_s16le", str(path.with_suffix(".mkv"))], check=True)
    path.with_suffix(".mkv").rename(path)
    return words


def _edl(src: Path, words, a=3.0, b=10.0):
    e = project.blank({"path": str(src), "url": None, "start": 0, "end": 20, "width": 1920, "height": 1080, "fps": 30,
                       "has_audio": True}, words, a, b)
    e["layout"]["keyframes"] = [{"t": a, "layout": "blur_fill", "cam": None, "path": [[0, 0.5]]}]
    e["music"]["enabled"] = False
    e["progress_bar"] = False
    return e


def _audio(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(float)


def _has_freq(x: np.ndarray, f: float) -> bool:
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(x.size, 1 / 48000)
    band = spec[(freqs > f - 15) & (freqs < f + 15)].max()
    return band > 0.05 * spec[freqs > 100].max()


# ---- edit projects ----------------------------------------------------------------------------------
def test_edit_project_saves_reloads_and_undo_redo(fresh_db):
    cid = db.ex("INSERT INTO clips(title) VALUES ('t')")
    e = project.blank({"path": "x", "start": 0, "end": 30, "width": 1920, "height": 1080, "has_audio": True},
                      [{"word": "hi", "start": 1, "end": 1.3}], 0, 20)
    p1 = project.create(cid, e)
    assert project.get(cid)["edl"] == e and not p1["can_undo"]
    p2 = project.update(cid, lambda x: {**x, "cuts": [[2, 3]]}, "cut")
    p3 = project.update(cid, lambda x: {**x, "range": [1, 15]}, "range")
    assert p3["head"] == 3 and p3["can_undo"]
    u = project.undo(cid)
    assert u["edl"]["range"] == [0, 20] and u["edl"]["cuts"] == [[2, 3]] and u["can_redo"]
    r = project.redo(cid)
    assert r["edl"]["range"] == [1, 15]
    project.undo(cid)
    project.undo(cid)
    b = project.update(cid, lambda x: {**x, "captions": {**x["captions"], "nudge": -40}}, "nudge")
    assert not b["can_redo"] and b["head"] == 2                      # new edit after undo drops the redo branch
    assert [h["label"] for h in project.history(cid)] == ["nudge", "Auto-edit"]
    assert p2["head"] == 2


def test_timeline_mapping():
    e = {"range": [10, 30], "cuts": [[12, 14], [20, 25]], "captions": {"words": []}}
    assert T.kept(e) == [(10, 12), (14, 20), (25, 30)]
    assert T.duration(e) == 13
    assert T.to_out(e, 15) == 3 and T.to_out(e, 13) is None and T.to_src(e, 3) == 15
    assert T.intervals_out(e, [(11, 26)]) == [(1, 2), (2, 8), (8, 9)]


@needs_ffmpeg
def test_render_is_identical_after_reload(fresh_db, tmp_path):
    src = tmp_path / "s.mkv"
    words = _tone_source(src)
    cid = db.ex("INSERT INTO clips(title) VALUES ('t')")
    project.create(cid, _edl(src, words))
    e1 = project.get(cid)["edl"]
    r1 = EC.render(e1, tmp_path / "a.mp4", tmp_path / "c1", "proxy")
    e2 = json.loads(json.dumps(project.get(cid)["edl"]))            # reload from the database
    r2 = EC.render(e2, tmp_path / "b.mp4", tmp_path / "c2", "proxy")

    def md5(p):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-map", "0:v", "-f", "md5", "-"],
                              capture_output=True, text=True, check=True).stdout
    assert md5(r1["path"]) == md5(r2["path"])


@needs_ffmpeg
def test_transcript_deletion_cuts_the_right_audio(tmp_path):
    src = tmp_path / "s.mkv"
    words = _tone_source(src)
    e = TR.delete_words(_edl(src, words), [2, 3])                    # w2 (700 Hz) and w3 (900 Hz)
    assert T.duration(e) == pytest.approx(7.0 - (words[4]["start"] - words[2]["start"]) + 2 * TR.PAD, abs=0.15)
    r = EC.render(e, tmp_path / "o.mp4", tmp_path / "c", "proxy")
    x = _audio(Path(r["path"]))
    assert _has_freq(x, 500) and _has_freq(x, 1100)
    assert not _has_freq(x, 700) and not _has_freq(x, 900)
    got = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                r["path"]], capture_output=True, text=True).stdout)
    assert abs(got - T.duration(e)) < 0.15


def test_remove_fillers_silences_range_and_fix():
    ws = [{"word": "so", "start": 1, "end": 1.3}, {"word": "um,", "start": 1.4, "end": 1.6},
          {"word": "we", "start": 1.7, "end": 1.9}, {"word": "won", "start": 4.0, "end": 4.4}]
    e = {"source": {"start": 0, "end": 10}, "range": [0.5, 6], "cuts": [], "captions": {"words": ws}}
    f = TR.remove_fillers(e)
    assert f["captions"]["words"][1]["deleted"] and f["cuts"] and T.duration(f) < T.duration(e)
    s = TR.remove_silences(e)
    assert any(a > 1.9 and b < 4.0 for a, b in s["cuts"])            # the 2.1 s gap is cut (keeps a little)
    assert T.duration(s) < T.duration(e) - 1.5
    r = TR.set_range(e, 0.9, 4.6)
    assert r["range"] == [0.9, 4.6]
    with pytest.raises(ValueError):
        TR.set_range(e, 3, 3.5)
    fx = TR.fix_word(e, 3, "WON!!")
    assert fx["captions"]["words"][3]["word"] == "WON!!"
    back = TR.restore_words(f, [1])
    assert not back["captions"]["words"][1].get("deleted") and back["cuts"] == []


@needs_ffmpeg
@pytest.mark.parametrize("preset", list(EC.PRESETS))
def test_exports_match_platform_presets(fresh_db, tmp_path, preset):
    src = tmp_path / "s.mkv"
    words = _tone_source(src)
    r = EC.render(_edl(src, words), tmp_path / f"{preset}.mp4", tmp_path / "c", "export", preset=preset)
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_type:format=duration,bit_rate",
                                      "-of", "json", r["path"]], capture_output=True, text=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert float(info["format"]["duration"]) <= EC.PRESETS[preset]["max_sec"]
    if EC.PRESETS[preset]["captions"] == "srt":
        assert Path(r["srt"]).exists() and "w1" in Path(r["srt"]).read_text()
    long = _edl(src, words, 0, 20)
    long["range"] = [0, 20]
    EC.PRESETS["_tiny"] = {"label": "Tiny", "max_sec": 5, "bitrate": "4M", "captions": "burned"}
    with pytest.raises(ValueError):
        EC.render(long, tmp_path / "x.mp4", tmp_path / "c", "export", preset="_tiny")
    EC.PRESETS.pop("_tiny")


# ---- captions: every template (v0.8.5: the same fixed place in every layout) ------------------------------
@needs_ffmpeg
@pytest.mark.parametrize("tmpl", [*C.PRESETS, {"base": "bold_pop", "family": "Anton", "em": 120, "animation": "bounce"},
                                  {"base": "minimal", "family": "Montserrat ExtraBold", "y": 0.55, "max_words": 2}])
def test_caption_centred_every_template(tmp_path, tmpl):
    import cv2
    ws = [{"word": w, "start": 1 + i * 0.5, "end": 1.4 + i * 0.5} for i, w in enumerate("NO WAY HE ACTUALLY WON".split())]
    tr = C.build_track(ws, tmp_path / "c.ass", tmpl, vocab=False)
    shutil.copytree(C.fonts_dir(), tmp_path / "fonts", dirs_exist_ok=True)
    bottoms = set()
    for ev in tr.events[::2]:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x808080:s=1080x1920:d=6", "-vf",
                        "ass=c.ass:fontsdir=fonts", "-ss", f"{ev['start'] + 0.12:.2f}", "-frames:v", "1", "f.png"],
                       cwd=tmp_path, check=True)
        img = cv2.imread(str(tmp_path / "f.png")).astype(int)
        ys, xs = np.nonzero(np.abs(img - 128).sum(axis=2) > 40)
        assert abs((xs.min() + xs.max()) / 2 - 540) / 1080 <= 0.01, (tmpl, ev["word"])
        assert C.SAFE["left"] - 10 <= xs.min() and xs.max() <= C.SAFE["right"] + 10
        assert 1100 <= ys.min() and ys.max() <= C.SAFE["bottom"]
        bottoms.add(int(ys.max()) // 4)
    assert len(bottoms) <= 2                                       # one baseline (±4 px of anti-aliasing)


def test_template_validation_and_no_animations(tmp_path):
    t = C.template_from({"base": "bold_pop", "text": "#ff00ff", "highlight": "nonsense", "em": 999, "family": "Comic Sans",
                         "animation": "spin", "max_words": 9})
    assert t.text == "FF00FF" and t.highlight == "FFE600" and t.em == 140 and t.family == "Montserrat Black"
    assert t.max_words == 6 and not hasattr(t, "animation")
    ws = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate("a b c".split())]
    for anim in ("typewriter", "karaoke", "bounce", "pop"):     # old saved templates: drawn static now
        txt = C.build_track(ws, tmp_path / f"{anim}.ass", {"base": "bold_pop", "animation": anim}, vocab=False).path.read_text()
        assert "\\alpha&HFF&" not in txt and "\\fscx118" not in txt and "\\t(" not in txt


def test_user_templates_and_defaults(fresh_db):
    t = templates.save("My TikTok", {"base": "hype_yellow", "highlight": "#00ff88"})
    assert t["key"].startswith("user:") and templates.get(t["key"])["highlight"] == "00FF88"
    templates.set_default("global", "minimal")
    templates.set_default("account:7", t["key"])
    templates.set_default("program:3", "clean_white")
    assert templates.default_for(3, [7]) == "clean_white"
    assert templates.default_for(None, [7]) == t["key"]
    assert templates.default_for(None, [9]) == "minimal"
    templates.delete(t["id"])
    assert templates.default_for(None, [7]) == "minimal"
    assert {x["key"] for x in templates.builtins()} == set(C.PRESETS)


def test_translation_keeps_timing(monkeypatch):
    from clipfarm.edit import translate
    import clipfarm.edit.translate as tmod
    ws = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate("no way he did that".split())]
    monkeypatch.setattr(tmod, "complete_json", lambda *a, **k: {"lines": ["de ninguna manera lo hizo"]})
    out = translate.translate_words(ws, "es")
    assert [w["word"] for w in out] == ["de", "ninguna", "manera", "lo", "hizo"]
    assert out[0]["start"] == 0 and out[-1]["end"] <= ws[-1]["end"] and all(w["end"] > w["start"] for w in out)
    with pytest.raises(ValueError):
        translate.translate_words(ws, "xx")


# ---- reframe ---------------------------------------------------------------------------------------
@needs_ffmpeg
def test_reframe_frames_the_action_without_moving(tmp_path):
    """A white square moving left -> right on a dark game: one fixed crop on where the action is (v0.8.5: the
    crop never pans to follow it)."""
    src = tmp_path / "act.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x202020:s=1920x1080:r=30:d=10",
                    "-f", "lavfi", "-i", "color=c=white:s=120x120:r=30:d=10", "-filter_complex",
                    "[0][1]overlay=x='700+t*50':y=480", "-c:v", "libx264", "-preset", "ultrafast", str(src)], check=True)
    lay = reframe.analyse(src, 0, 10, detect_cam=False)
    kf = lay["keyframes"][0]
    assert kf["layout"] == "crop" and len(kf["path"]) == 1          # one fixed crop
    crop_w = 1080 * 9 / 16 / 1920
    cx = kf["path"][0][1]
    inside = [abs((700 + t * 50 + 60) / 1920 - cx) < crop_w / 2 for t in np.arange(1, 9.5, 0.5)]
    assert sum(inside) / len(inside) >= 0.9


@needs_ffmpeg
def test_reframe_never_crops_a_required_overlay(tmp_path):
    src = tmp_path / "act.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=6", "-c:v",
                    "libx264", "-preset", "ultrafast", str(src)], check=True)
    lay = reframe.analyse(src, 0, 6, onscreen=["streamer_overlay"], detect_cam=False)
    assert all(k["layout"] == "blur_fill" for k in lay["keyframes"])
    kfs, fixed = reframe.enforce_rules([{"t": 0, "layout": "crop", "path": [[0, .3]]}], ["streamer_overlay"], None)
    assert kfs[0]["layout"] == "blur_fill" and fixed
    kfs, fixed = reframe.enforce_rules([{"t": 0, "layout": "speaker", "path": [[0, .3]]}], ["streamer_face"],
                                       {"x": .7, "y": 0, "w": .3, "h": .3})
    assert kfs[0]["layout"] == "corner" and kfs[0]["cam"]          # v0.8.5: facecam in the corner


def test_speaker_tracking_picks_who_is_talking():
    import cv2  # noqa: F401
    frames = []
    rng = np.random.default_rng(0)
    for i in range(10):
        f = np.full((180, 320, 3), 90, np.uint8)
        f[120:150, 230:270] = rng.integers(0, 255, (30, 40, 3), dtype=np.uint8)   # right person's mouth moves
        frames.append((i * 0.5, f))
    faces = lambda f: [(0.25, 0.6, 0.2, 0.3), (0.78, 0.6, 0.2, 0.3)]            # noqa: E731
    path = reframe.speaker_path(frames, [(0, 10)], faces)
    assert np.mean([x for _, x in path[1:]]) > 0.7


def test_scene_classification_screen_vs_gameplay():
    import cv2
    still = np.zeros((180, 320, 3), np.uint8)
    for k in range(12):
        cv2.rectangle(still, (10 + (k % 6) * 50, 20 + (k // 6) * 70), (50 + (k % 6) * 50, 80 + (k // 6) * 70), (255, 255, 255), 1)
    assert reframe.classify([(i * .5, still) for i in range(8)], None, lambda f: []) == "screen"
    rng = np.random.default_rng(1)
    moving = [(i * .5, rng.integers(0, 255, (180, 320, 3), dtype=np.uint8)) for i in range(8)]
    assert reframe.classify(moving, None, lambda f: []) == "gameplay"


# ---- legacy clips + migration -------------------------------------------------------------------------
@needs_ffmpeg
def test_old_clip_gets_an_edit_project(fresh_db, tmp_path):
    from clipfarm.edit import build
    base = tmp_path / "x.base.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=30:d=6", "-f", "lavfi",
                    "-i", "anullsrc", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(base)],
                   check=True)
    cid = db.ex("""INSERT INTO clips(title, words, caption_preset, render_spec, hook_text) VALUES ('t',?,?,?,?)""",
                (json.dumps([{"word": "hey", "start": 11, "end": 11.4}]), "minimal",
                 json.dumps({"base": str(base), "start": 10, "credit": "twitch.tv/x"}), "Hook!"))
    p = build.ensure_project(cid)
    assert p and p["edl"]["captions"]["template"] == "minimal" and p["edl"]["captions"]["words"][0]["start"] == 1
    assert p["edl"]["hook"]["text"] == "Hook!" and p["edl"]["rules"]["credit"] == "twitch.tv/x"
    assert db.ex("INSERT INTO clips(title) VALUES ('gone')") and build.ensure_project(
        db.q1("SELECT MAX(id) i FROM clips")["i"]) is None


def test_v6_migration_keeps_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    path = tmp_path / "v5.db"
    db.init_db(path, upto=5)
    cid = db.ex("INSERT INTO clips(title, moment_type) VALUES ('kept', 'Funny')")
    db.init_db(path)
    assert migrations.current_version(db.connect()) == migrations.LATEST >= 6
    assert db.q1("SELECT title, moment_type FROM clips WHERE id=?", (cid,)) == {"title": "kept", "moment_type": "Funny"}
    for t in ("edit_projects", "edit_versions", "caption_templates"):
        assert db.q1("SELECT name FROM sqlite_master WHERE name=?", (t,))
