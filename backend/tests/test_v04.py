"""v0.4 tests: captions (centring, safe zones, facecam/overlay clearance, word sync), highlight scoring,
learning from ratings, music (licensed only, no-music campaigns, ducking, -14 LUFS), trending sounds."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipfarm import db, migrations
from clipfarm.editing import captions as C

HAS_FFMPEG = shutil.which("ffmpeg") is not None
ROOT = Path(__file__).resolve().parents[2]
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
WORDS = [{"word": w, "start": 1.0 + i * 0.45, "end": 1.4 + i * 0.45}
         for i, w in enumerate("NO WAY he just jumped the ABSOLUTELY UNBELIEVABLE gap".split())]


def _frame(ass: Path, t: float, w: int = 1080, h: int = 1920) -> np.ndarray:
    import cv2
    work = ass.parent
    shutil.copytree(C.fonts_dir(), work / "fonts", dirs_exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x808080:s={w}x{h}:d=12",
                    "-vf", f"ass={ass.name}:fontsdir=fonts", "-ss", f"{t:.2f}", "-frames:v", "1", "f.png"],
                   cwd=work, check=True)
    return cv2.imread(str(work / "f.png"))


def _bbox(img: np.ndarray):
    m = np.abs(img.astype(int) - 128).sum(axis=2) > 40
    ys, xs = np.nonzero(m)
    return (xs.min(), xs.max(), ys.min(), ys.max()) if xs.size else None


# ---- captions (v0.8.5: one fixed place for every layout; see test_v085 for the full set) -------------------
@needs_ffmpeg
def test_captions_centred_and_inside_safe_zone(tmp_path):
    tr = C.build_track(WORDS, tmp_path / "c.ass", "bold_pop", vocab=False)
    for ev in tr.events:
        img = _frame(tr.path, ev["start"] + 0.12)
        x0, x1, y0, y1 = _bbox(img)
        assert abs((x0 + x1) / 2 - 540) / 1080 <= 0.01, (ev["word"], x0, x1)
        assert C.SAFE["left"] - 10 <= x0 and x1 <= C.SAFE["right"] + 10, (ev["word"], x0, x1)
        assert 1100 <= y0 and y1 <= C.SAFE["bottom"], (ev["word"], y0, y1)
        assert ev["lines"] <= 2


@needs_ffmpeg
def test_hook_and_credit_in_top_safe_zone(tmp_path):
    tr = C.build_track(WORDS[:2], tmp_path / "c.ass", "bold_pop", hook="He actually did it no way",
                       credit="twitch.tv/tester", duration=8, vocab=False)
    img = _frame(tr.path, 0.5)
    top = img[:900]
    x0, x1, y0, y1 = _bbox(top)
    assert abs((x0 + x1) / 2 - 540) <= 22 and y0 >= C.SAFE["top"] - 30 and y1 < 700


def test_word_highlight_sync_within_100ms(tmp_path):
    words = [{"word": f"w{i}", "start": 3.0 + i * 0.37, "end": 3.3 + i * 0.37} for i in range(9)]
    tr = C.build_track(words, tmp_path / "c.ass", offset=2.0, vocab=False)
    assert len(tr.events) == len(words)
    for w, ev in zip(words, tr.events):
        assert abs(ev["start"] - (w["start"] - 2.0)) <= 0.1
        assert sum(sum(r) for r in ev["hi"]) == 1                                # exactly one word highlighted
    text = tr.path.read_text()
    starts = sorted({l.split(",")[1] for l in text.splitlines() if l.startswith("Dialogue: 0,")})
    expect = sorted({C._ts(w["start"] - 2.0) for w in words})
    assert starts == expect                                                     # centisecond-exact in the file


@needs_ffmpeg
def test_highlight_colour_visible_on_spoken_word(tmp_path):
    words = [{"word": "HELLO", "start": 1.0, "end": 1.5}, {"word": "THERE", "start": 1.6, "end": 2.1}]
    tr = C.build_track(words, tmp_path / "c.ass", "bold_pop", vocab=False)
    img = _frame(tr.path, 1.75)                                                 # THERE is being spoken
    yellow = (img[..., 2] > 200) & (img[..., 1] > 180) & (img[..., 0] < 80)
    ys, xs = np.nonzero(yellow)
    assert xs.size and xs.mean() > 540                                          # the right-hand word is lit


@needs_ffmpeg
def test_no_caption_during_silence(tmp_path):
    words = [{"word": "first", "start": 1.0, "end": 1.4}, {"word": "later", "start": 5.0, "end": 5.4}]
    tr = C.build_track(words, tmp_path / "c.ass", "bold_pop")
    assert _bbox(_frame(tr.path, 3.0)) is None


@pytest.mark.parametrize("style", list(C.PRESETS))
def test_long_words_wrap_to_two_lines_and_fit(tmp_path, style):
    ws = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in
          enumerate(["UNBELIEVABLE", "EXTRAORDINARY", "CATASTROPHE", "no", "way", "INTERNATIONALLY"])]
    pr = C.preset(style)
    tr = C.build_track(ws, tmp_path / "c.ass", style, vocab=False)
    assert len(tr.events) == len(ws)
    for cap in C.captions_from(ws, pr, pr.max_words, True):
        assert len(cap["lines"]) <= 2
        k = cap.get("condense", 1.0)                       # a single over-wide word is narrowed, never shrunk
        for row in cap["lines"]:
            assert C.drawn_width(" ".join(w["word"].upper() for w in row), pr) * k \
                <= C.CAP_WIDTH + 1


def test_profanity_and_fillers():
    ws = [{"word": "um", "start": 0, "end": .2}, {"word": "fucking", "start": .3, "end": .7},
          {"word": "nice", "start": .8, "end": 1}]
    shown, bleeps = C.clean_words(ws, "bleep", "remove")
    assert [w["word"] for w in shown] == ["f******", "nice"] and bleeps == [(.3, .7)]
    shown, bleeps = C.clean_words(ws, "keep", "keep")
    assert [w["word"] for w in shown] == ["um", "fucking", "nice"] and not bleeps


def test_presets_and_font_licences():
    keys = {p["key"] for p in C.presets_public()}
    assert {"bold_pop", "hype_yellow", "clean_white", "minimal"} <= keys
    d = C.fonts_dir()
    for fam, f in C.FONTS.items():
        assert (d / f).exists()
    for lic in ("OFL-Anton.txt", "OFL-ArchivoBlack.txt", "OFL-Montserrat.txt"):
        assert "SIL Open Font License" in (d / lic).read_text(encoding="utf-8")


def test_emoji_off_by_default_and_licensed(tmp_path):
    tr = C.build_track([{"word": "laugh", "start": 0, "end": .5}], tmp_path / "c.ass")
    assert tr.emojis == []
    tr = C.build_track([{"word": "laugh", "start": 0, "end": .5}], tmp_path / "d.ass", emoji=True)
    assert tr.emojis and (ROOT / "backend/assets/emoji" / f"emoji_u{tr.emojis[0]['code']}.png").exists()
    assert (ROOT / "backend/assets/emoji/LICENSE-noto-emoji.txt").exists()


@needs_ffmpeg
@pytest.mark.parametrize("layout", ["crop", "blur_fill", "split"])
def test_rendered_frame_caption_centred(tmp_path, layout):
    """Full render (both passes): caption pixels in the output frame are centred."""
    from clipfarm.editing.layout import Box
    from clipfarm.editing.render import RenderSpec, render
    src = tmp_path / "flat.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x404040:s=1920x1080:r=30:d=8",
                    "-f", "lavfi", "-i", "sine=f=220:d=8", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(src)], check=True)
    words = [{"word": w, "start": 2 + i * 0.4, "end": 2.35 + i * 0.4} for i, w in enumerate("WAIT FOR IT".split())]
    common = dict(layout=layout, facecam=Box(0.7, 0.0, 0.3, 0.3) if layout == "split" else None, quality="fast",
                  progress_bar=False)
    a = render(RenderSpec(src, 0, 6, tmp_path / "a.mp4", words=words, **common))
    b = render(RenderSpec(src, 0, 6, tmp_path / "b.mp4", words=[], **common))
    import cv2

    def grab(p):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "2.5", "-i", p, "-frames:v", "1", str(p) + ".png"], check=True)
        return cv2.imread(str(p) + ".png").astype(int)
    diff = np.abs(grab(a["path"]) - grab(b["path"])).sum(axis=2) > 60
    ys, xs = np.nonzero(diff)
    assert xs.size
    assert abs((xs.min() + xs.max()) / 2 - 540) / 1080 <= 0.02, (layout, xs.min(), xs.max())


# ---- highlight scoring (2E unit tests) --------------------------------------------------------------
def _signals(n=600, seed=1):
    from clipfarm.highlights import detect
    rng = np.random.default_rng(seed)
    ev = {k: (rng.random(n) * 0.05).astype(np.float32) for k in
          ("laughter", "scream", "cheer", "explosion", "gunshot", "crash", "vehicle", "speech", "music", "silence")}
    loud = (-30 + rng.normal(0, 2, n)).astype(np.float32)
    vis = {k: np.zeros(n, np.float32) for k in ("motion", "cuts", "flash", "shake", "static", "dark", "ui")}
    vis["motion"][:] = 0.03 + rng.random(n) * 0.01
    return detect, ev, loud, vis


def _curve(ev, loud, vis, chat=None, n=600):
    from clipfarm.highlights import detect
    comp, parts = detect.components(detect.Signals(n, events=ev, loudness=loud, chat=chat))
    return detect.curve(comp, parts), comp, parts


def test_laughter_and_explosions_raise_the_score():
    detect, ev, loud, vis = _signals()
    base, _, _ = _curve(ev, loud, vis)
    ev["laughter"][100:108] = 0.9
    ev["explosion"][250:256] = 0.85
    vis["flash"][250:256] = 0.3
    cv, _, _ = _curve(ev, loud, vis)
    assert cv[100:108].max() > base[100:108].max() + 0.4
    assert cv[250:256].max() > base[250:256].max() + 0.4


def test_loading_music_and_quiet_are_penalised():
    # v0.8.9: no video analysis, so menus are no longer seen; loading music and dead air still are
    detect, ev, loud, vis = _signals()
    ev["music"][380:440] = 0.9                          # loading music
    ev["silence"][500:560] = 0.95                       # dead air
    cv, _, parts = _curve(ev, loud, vis)
    assert parts["penalty"][390:430].mean() > 0.3 and parts["penalty"][510:550].mean() > 0.4
    assert cv[390:430].max() < 0.1 and cv[510:550].max() < 0.1


def test_constantly_loud_segment_without_events_is_not_picked():
    detect, ev, loud, vis = _signals()
    loud[200:420] = -10                                  # loud streamer / loud music for minutes
    ev["speech"][200:420] = 0.9
    ev["laughter"][500:508] = 0.9                        # one real laugh elsewhere
    cv, comp, parts = _curve(ev, loud, vis)
    picks = detect.pick(cv, parts, comp, loud, budget=5)
    assert picks and all(not (200 <= m.peak <= 420) for m in picks)
    assert any(495 <= m.peak <= 512 for m in picks)


def test_chat_reactions_count_relative_to_channel_rate():
    from clipfarm.highlights import signals
    msgs = [(t * 0.5, "hello chat") for t in range(1200)]                       # busy normal chat
    msgs += [(300 + t * 0.1, "KEKW LUL") for t in range(60)]                    # laugh spike
    ch = signals.chat_reactions(msgs, 600)
    assert ch["laugh"][300:306].sum() > 40 and ch["laugh"][:290].sum() == 0
    detect, ev, loud, vis = _signals()
    cv, comp, _ = _curve(ev, loud, vis, chat=ch)
    assert comp["chat"][300:312].max() > 0.6 > comp["chat"][100:200].max()


def test_moment_types_and_focus():
    detect, ev, loud, vis = _signals()
    ev["laughter"][100:108] = 0.9
    ev["explosion"][250:256] = 0.85
    vis["flash"][250:256] = 0.3
    n = 600
    comp, parts = detect.components(detect.Signals(n, events=ev, loudness=loud))
    assert detect.classify(parts, 98, 110) == "Funny"
    assert detect.classify(parts, 248, 258) == "Action + Reaction"
    cf, _ = detect.components(detect.Signals(n, events=ev, loudness=loud), "funny")
    ca, _ = detect.components(detect.Signals(n, events=ev, loudness=loud), "action")
    assert cf["audio_events"][100:108].max() > ca["audio_events"][100:108].max()
    assert ca["audio_events"][250:256].max() >= cf["audio_events"][250:256].max()


def test_window_starts_before_buildup_not_mid_laugh():
    detect, ev, loud, vis = _signals()
    ev["laughter"][200:210] = 0.9
    loud[195:210] = -18
    cv, comp, parts = _curve(ev, loud, vis)
    m = detect.pick(cv, parts, comp, loud, budget=1)[0]
    assert m.start <= 200 - 1                       # starts before the laugh does
    assert m.end >= 210                             # and ends after it settles
    assert 15 <= m.end - m.start <= 60


def test_learning_reweights_after_enough_ratings(fresh_db):
    from clipfarm.highlights import learning
    sid = db.ex("INSERT INTO streamers(platform, platform_user_id, login, display_name) VALUES ('twitch','1','x','X')")
    gid = db.ex("INSERT INTO games(name) VALUES ('G')")
    src = db.ex("INSERT INTO sources(streamer_id, kind, platform_id, url) VALUES (?, 'vod', 'v', 'u')", (sid,))
    rng = np.random.default_rng(3)
    for i in range(60):
        chat = float(rng.random())
        sig = {"audio_events": float(rng.random()), "chat": chat, "visual": float(rng.random()), "community": 0.0}
        mid = db.ex("INSERT INTO moments(source_id, start_sec, end_sec, score, signals) VALUES (?,0,20,0.5,?)",
                    (src, json.dumps(sig)))
        cid = db.ex("INSERT INTO clips(moment_id, streamer_id, game_id) VALUES (?,?,?)", (mid, sid, gid))
        learning.record(cid, 1 if chat > 0.5 else -1)                       # you only like high-chat clips
    w = learning.weights_for(sid, gid)
    assert w["chat"] > 0.25 and abs(sum(w.values()) - 1) < 0.01
    learning.reset(f"streamer:{sid}")
    learning.reset(f"game:{gid}")
    assert learning.weights_for(sid, gid)["chat"] == 0.40          # v0.8.9: chat carries the old visual weight


def test_yamnet_features_shape_and_mel_matrix():
    from clipfarm.highlights import audio_events as A
    w = np.random.default_rng(0).standard_normal(16000 * 5).astype(np.float32) * 0.1
    assert A.patches(w).shape == (10, 96, 64)
    m = A.mel_matrix()
    assert m.shape == (257, 64) and m[0].sum() == 0 and (m >= 0).all()


# ---- music ---------------------------------------------------------------------------------------
@needs_ffmpeg
def test_bundled_music_is_licensed_and_tagged(fresh_db):
    from clipfarm.music import library
    library.ensure_bundled()
    ts = library.tracks()
    assert len(ts) >= 8
    for t in ts:
        assert t["source"] == "bundled" and t["licence"].startswith("CC0") and t["exists"]
        assert t["moods"] and t["bpm"] and t["beats"] and t["drop_at"] is not None and t["loudness"] < 0
    moods = {m for t in ts for m in t["moods"]}
    assert {"funny", "hype", "tense", "epic", "chill", "sad"} <= moods


@needs_ffmpeg
def test_music_matches_moment_type_and_never_repeats_on_an_account(fresh_db):
    from clipfarm.music import pick
    a = pick.choose("Funny", [7], seed=1)
    assert "funny" in a["moods"]
    assert "hype" in pick.choose("Action/Explosion", [7], seed=1)["moods"]
    pick.record_use(a["id"], None, [7])
    b = pick.choose("Funny", [7], seed=1)
    assert b["id"] != a["id"]
    p = pick.plan(pick.choose("Action/Explosion", [7], seed=2), "Action/Explosion", payoff=6.0, duration=20)
    assert p["entry"] == 6.0 and p["mode"] == "drop on payoff"
    assert any(abs(bt - 6.0) < 0.02 for bt in p["beats"])                     # a beat lands on the payoff


def test_no_music_campaigns_and_original_audio_only(fresh_db):
    from clipfarm.campaigns import importer
    from clipfarm.music import pick
    p = importer.parse("Big Campaign\nRules: Original audio only. No background music.\n$1.00 / 1K")
    assert p.get("no_music") == 1
    ok, why = pick.music_allowed({"name": "X", "no_music": 1}, {})
    assert not ok and "original audio" in why
    assert pick.music_allowed(None, {"original_audio_only": True})[0] is False
    assert pick.music_allowed(None, {})[0] is True


@needs_ffmpeg
def test_speech_is_ducked_and_mix_is_minus_14_lufs(tmp_path, fresh_db):
    from clipfarm.editing.render import RenderSpec, build_audio, integrated_lufs, music_gain_db, render
    src = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=1280x720:r=30:d=12",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "12", "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", str(src)], check=True)
    mus = tmp_path / "m.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.2:d=20", "-ac", "2",
                    str(mus)], check=True)
    spec = RenderSpec(src, 0, 10, tmp_path / "o.mp4", layout="blur_fill", music=mus, music_track_lufs=-20,
                      speech=[(3.0, 6.0)], duck_db=15, quality="fast")
    assert music_gain_db(4.5, spec) == pytest.approx(-15, abs=0.01) and music_gain_db(8.5, spec) == 0
    from clipfarm.editing.render import render_base
    base, meta = render_base(spec)
    wav = build_audio(spec, base, meta, [], tmp_path)
    import wave
    with wave.open(str(wav)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 2).astype(float)
    rms = lambda a, b: 20 * np.log10(np.sqrt((x[int(a * 48000):int(b * 48000)] ** 2).mean()) + 1e-9)  # noqa: E731
    drop = rms(7.5, 9.5) - rms(3.5, 5.5)
    assert 12 <= drop <= 18, drop                                                  # ducked by 12–18 dB
    out = render(spec)
    assert abs(integrated_lufs(Path(out["path"])) + 14) <= 1.0


def test_only_library_music_can_be_used_and_no_trending_audio(fresh_db, tmp_path):
    """Music only comes from the licensed library; anything else in an edit project is dropped at render."""
    from clipfarm.edit import compile as EC
    from clipfarm.music import library
    library.ensure_bundled()
    t = library.tracks()[0]
    assert EC.licensed(t["path"])
    stray = tmp_path / "some_trending_song.mp3"
    stray.write_bytes(b"x")
    assert not EC.licensed(str(stray))
    db.ex("UPDATE music_tracks SET enabled=0 WHERE id=?", (t["id"],))
    assert not EC.licensed(t["path"])                                              # disabled = not usable
    build = (ROOT / "backend/clipfarm/edit/build.py").read_text(encoding="utf-8")
    assert "music_pick.choose(" in build and 'track["path"]' in build
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "backend/clipfarm").rglob("*.py"))
    assert not re.search(r"(tiktok|instagram)\.com/[^\"']*(music|audio|sound)", src, re.I)
    tr = (ROOT / "backend/clipfarm/music/trending.py").read_text(encoding="utf-8")
    assert "download" not in tr.split('"""', 2)[2].lower() and "requests" not in tr


def test_trending_list_and_suggestions(fresh_db):
    from clipfarm.music import trending
    r = trending.import_text("Funny Song - Somebody | funny | tiktok\nHype Beat - Producer | hype\n# comment\n")
    assert r["added"] == 2
    assert trending.suggest("Funny")[0]["name"] == "Funny Song"
    assert trending.suggest("Action/Explosion")[0]["name"] == "Hype Beat"
    assert db.q1("PRAGMA table_info(trending_sounds)") and "path" not in {
        c["name"] for c in db.q("PRAGMA table_info(trending_sounds)")}


# ---- migration --------------------------------------------------------------------------------------
def test_v5_migration_keeps_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    path = tmp_path / "v4.db"
    db.init_db(path, upto=4)
    db.ex("UPDATE programs SET rules='Original audio only please' WHERE streamer_label='KreekCraft'")
    n = db.q1("SELECT COUNT(*) n FROM programs")["n"]
    db.init_db(path)
    assert migrations.current_version(db.connect()) == migrations.LATEST >= 5
    assert db.q1("SELECT COUNT(*) n FROM programs")["n"] == n
    assert db.q1("SELECT no_music FROM programs WHERE streamer_label='KreekCraft'")["no_music"] == 1
    for t in ("clip_feedback", "music_tracks", "music_usage", "trending_sounds"):
        assert db.q1("SELECT name FROM sqlite_master WHERE name=?", (t,))
    cols = {c["name"] for c in db.q("PRAGMA table_info(clips)")}
    assert {"words", "moment_type", "music_track_id", "base_path", "render_spec"} <= cols


def test_sub_train_chat_is_not_a_highlight():
    from clipfarm.highlights import detect, signals
    msgs = [(t * 0.5, "hi chat") for t in range(1200)]
    msgs += [(300 + t * 0.15, "KEKW LUL") for t in range(60)]                          # real laugh reaction
    msgs += [(450 + t * 0.17, "x gifted 5 Tier 1 subs!" if t % 3 == 0 else "PogChamp W") for t in range(120)]
    ch = signals.chat_reactions(msgs, 600)
    assert ch["announce"][450:470].sum() > 10
    comp, _ = detect.components(detect.Signals(600, chat=ch))
    assert comp["chat"][300:312].max() > 0.8
    assert comp["chat"][440:480].max() < 0.35


def test_feedback_on_clip_without_streamer_or_game(fresh_db):
    from clipfarm.highlights import learning
    cid = db.ex("INSERT INTO clips(title) VALUES ('orphan')")
    assert learning.record(cid, 1, ["great timing"]) == {"refit": []}
