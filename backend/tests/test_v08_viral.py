"""v0.8 step 2: viral moment detection learned from what actually goes viral."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

from clipfarm import db
from clipfarm.highlights import detect, events, features, run, signals, viral_data, viral_model

PKG = Path(__file__).resolve().parents[1] / "clipfarm"
N = 420


def _scene():
    """A synthetic VOD (per-second signals) with every kind of moment the brief names."""
    ev = {k: np.full(N, 0.02, np.float32) for k in ("laughter", "scream", "cheer", "explosion", "gunshot", "crash",
                                                    "vehicle", "siren", "driving", "sad", "speech", "music", "silence")}
    ev["speech"][:] = 0.55
    loud = np.full(N, -22.0, np.float32)
    msgs = [(t * 0.8, "hi") for t in range(int(N / 0.8))]
    vis = {k: np.full(N, 0.05, np.float32) for k in ("motion", "cuts", "flash", "shake", "static", "dark", "ui")}
    words = []
    # 20-50 explosion + laughter + chat laughing  (the best)
    ev["explosion"][30:33] = 0.9
    vis["flash"][30:32] = 0.2
    ev["laughter"][32:40] = 0.8
    loud[32:40] = -12
    msgs += [(33 + i * 0.15, "KEKW LUL 💀") for i in range(60)]
    words += [{"word": w, "start": 31 + i * 0.4, "end": 31.3 + i * 0.4} for i, w in enumerate("no way bro the car exploded".split())]
    # 80-110 explosion alone (nobody reacts)
    ev["explosion"][90:93] = 0.9
    vis["flash"][90:92] = 0.2
    # 140-170 funny talk (laughing, chat laughing)
    ev["laughter"][150:156] = 0.7
    msgs += [(151 + i * 0.25, "LMAO") for i in range(30)]
    # 200-240 sad talk, chat sending condolences
    ev["sad"][205:235] = 0.4
    msgs += [(206 + i * 0.2, "F o7 Sadge") for i in range(120)]
    # 260-320 calm driving
    ev["driving"][260:320] = 0.7
    ev["speech"][260:320] = 0.1
    # 330-370 menu
    vis["static"][330:370] = 0.95
    vis["ui"][330:370] = 0.9
    ev["music"][330:370] = 0.7
    ev["speech"][330:370] = 0.0
    # 380-410 cutscene: dialogue + music, no reaction
    ev["music"][380:410] = 0.6
    ev["speech"][380:410] = 0.8
    return ev, loud, msgs, vis, words


SEGMENTS = {"explosion+laugh": (20, 50), "explosion alone": (80, 110), "funny talk": (140, 170), "sad talk": (200, 240),
            "calm driving": (260, 320), "menu": (330, 370), "cutscene": (380, 410)}


def _features_by_segment():
    ev, loud, msgs, vis, words = _scene()
    sig = detect.Signals(N, events=ev, loudness=loud, chat=signals.chat_reactions(msgs, N), visual=vis)
    comp, parts = detect.components(sig)
    extra = features.per_second(parts, comp, N, words)
    tags = events.from_signals(N, parts, words, msgs)
    return {k: features.window(parts, comp, extra, tags, a, b) for k, (a, b) in SEGMENTS.items()}, tags


def test_explosion_with_laughter_beats_explosion_alone():
    f, _ = _features_by_segment()
    s = {k: viral_model.score(v)["score"] for k, v in f.items()}
    assert s["explosion+laugh"] > s["explosion alone"] + 25, s
    assert s["explosion+laugh"] >= 70, s
    assert f["explosion+laugh"]["explosion_x_laugh"] > 0.4 and f["explosion alone"]["explosion_x_laugh"] < 0.1


@pytest.mark.parametrize("seg", ["sad talk", "calm driving", "menu", "cutscene"])
def test_mundane_and_sad_never_score_high(seg):
    f, _ = _features_by_segment()
    r = viral_model.score(f[seg])
    assert r["score"] < 20, (seg, r["score"], f[seg])


def test_end_to_end_picks_and_reasons(monkeypatch, tmp_path):
    ev, loud, msgs, vis, words = _scene()
    monkeypatch.setattr(signals, "audio_rms", lambda media: loud)
    monkeypatch.setattr(run.signals, "audio_rms", lambda media: loud)
    monkeypatch.setattr(run.audio_events, "analyse", lambda media: ev)
    picks, dbg = run.detect_source(tmp_path / "x.wav", chat=msgs, budget=5, min_len=10, max_len=45,
                                   visual_fn=lambda a, b: {k: v[int(a):int(b)] for k, v in vis.items()},
                                   words_fn=lambda a, b: [w for w in words if a <= w["start"] < b], threshold=0.3)
    assert picks, "nothing picked"
    best = max(picks, key=lambda m: m.viral)
    assert best.start <= 32 <= best.end, (best.start, best.end)
    for m in picks:
        for name in ("sad talk", "calm driving", "menu", "cutscene"):
            a, b = SEGMENTS[name]
            assert not (a <= (m.start + m.end) / 2 <= b), f"picked {name}: {m.start}-{m.end} ({m.reason})"
    assert any(t["tag"] == "explosion" for t in best.tags)
    assert "Explosion at" in best.reason and "laugh" in best.reason, best.reason
    assert best.type in viral_model.TYPES and best.why and best.why[0]["contribution"] != 0
    # boundaries: start 1-3 s before the setup (the explosion), end after the reaction
    assert 26 <= best.start <= 30 and best.end >= 40


def test_reason_and_why_panel():
    f, _ = _features_by_segment()
    r = viral_model.score(f["explosion+laugh"])
    tags = [{"tag": "explosion", "t": 6, "strength": 0.9}]
    txt = viral_model.reason(f["explosion+laugh"], r, tags, chat_ratio=6.2, trend_tag="explosion")
    assert txt.startswith("Explosion at 0:06") and "streamer laughs" in txt and "chat 6× normal" in txt
    rows = viral_model.explain(f["explosion+laugh"], r)
    assert rows == sorted(rows, key=lambda x: -abs(x["contribution"]))
    sad = viral_model.explain(_features_by_segment()[0]["sad talk"], viral_model.score(_features_by_segment()[0]["sad talk"]))
    assert sad[0]["feature"] == "hard_penalty"


def test_types():
    f, _ = _features_by_segment()
    assert viral_model.moment_type(f["funny talk"]) == "Funny"
    assert viral_model.moment_type(f["explosion+laugh"]) in ("Funny", "Action + Reaction")
    assert viral_model.moment_type(f["explosion+laugh"], "Action/Explosion") == "Action + Reaction"   # old names ok
    assert set(viral_model.TYPES) == {"Funny", "Chaos/Fail", "Action + Reaction", "Rage", "Clutch/Skill", "Wholesome"}


# ---- training ----------------------------------------------------------------------------------------------
def _synthetic_examples(n_days=4, per_day=40, seed=0):
    rng = np.random.default_rng(seed)
    gid = db.ex("INSERT INTO games(name, twitch_id) VALUES ('Grand Theft Auto VI','1')")
    for d in range(n_days):
        for i in range(per_day):
            f = {k: float(rng.uniform(0, 0.3)) for k in features.FEATURES}
            viral = rng.random() < 0.4
            if viral:      # this game's viral clips: NPC fights + reactions (the defaults underweight NPCs)
                f.update(npc=float(rng.uniform(0.6, 1)), reaction=float(rng.uniform(0.5, 1)))
                f["event_x_reaction"] = f["npc"] * f["reaction"]
            else:
                f.update(laugh=float(rng.uniform(0.3, 0.7)))         # decoy: some laughing in non-viral windows
            viral_data.add_example(game_id=gid, streamer_id=None, kind="twitch_clip" if viral else "negative",
                                   platform_id=f"c{d}_{i}", label=1.0 if viral else 0.0, features=f,
                                   day=f"2026-11-{10 + d:02d}", vod_offset=float(i))
    return gid


def test_training_learns_per_game_and_reports_holdout(fresh_db):
    gid = _synthetic_examples()
    res = viral_model.train(gid)
    assert res["trained"] and res["n_pos"] > 20
    m = res["metrics"]
    assert m["holdout_days"] == ["2026-11-13"] and m["test_examples"] == 40
    assert m["auc"] > m["default_auc"] and m["auc"] > 0.85
    assert m["precision_at_10"] >= m["default_precision_at_10"]
    model = viral_model.load(gid)
    assert model["weights"]["npc"] > viral_model.DEFAULT["npc"]
    viral_model.reset(gid)
    assert viral_model.load(gid)["trained"] is False


def test_little_data_stays_near_the_defaults(fresh_db):
    X = np.zeros((6, len(features.FEATURES)))
    y = np.array([1, 0, 1, 0, 1, 0], float)
    w, b = viral_model.fit(X + np.eye(6, len(features.FEATURES)), y)
    drift = max(abs(w[k] - viral_model.DEFAULT[k]) for k in features.FEATURES)
    assert drift < 0.6


def test_not_enough_data_keeps_the_default(fresh_db):
    assert viral_model.train(None)["trained"] is False
    assert viral_model.load(None)["weights"] == viral_model.DEFAULT


def test_trend_tags(fresh_db):
    for i, tags in enumerate([["police", "chase"], ["police"], ["explosion"]]):
        viral_data.add_example(game_id=1, streamer_id=None, kind="twitch_clip", platform_id=f"t{i}", label=1, features={},
                               tags=tags)
    t = viral_model.trend_tags(1)
    assert list(t)[0] == "police" and t["police"] == 1.0 and t["chase"] == 0.5


# ---- dataset rules --------------------------------------------------------------------------------------------
def test_dataset_only_uses_permitted_streamers(fresh_db):
    a = db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                 VALUES ('twitch','111','ok','Ok','allowed')""")
    db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
             VALUES ('twitch','222','no','No','prohibited')""")
    db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
             VALUES ('twitch','333','unk','Unk','unknown')""")
    permitted = viral_data.permitted_streamers()
    assert permitted == {"111": a}
    clips = [{"id": "c1", "broadcaster_id": "111", "video_id": "v1", "vod_offset": 100, "views": 50},
             {"id": "c2", "broadcaster_id": "222", "video_id": "v2", "vod_offset": 100, "views": 900},
             {"id": "c3", "broadcaster_id": "333", "video_id": "v3", "vod_offset": 100, "views": 800},
             {"id": "c4", "broadcaster_id": "111", "video_id": None, "vod_offset": None, "views": 700},
             {"id": "c5", "broadcaster_id": "111", "video_id": "v1", "vod_offset": 900, "views": 500}]
    sel = viral_data.select_clips(clips, permitted)
    assert [c["id"] for c in sel] == ["c5", "c1"] and all(c["streamer_id"] == a for c in sel)


def test_dataset_never_fetches_tiktok_or_instagram():
    for f in ("highlights/viral_data.py", "highlights/events.py", "providers/twitch.py", "providers/youtube.py"):
        src = (PKG / f).read_text(encoding="utf-8").lower()
        assert not re.search(r"https?://[^\s\"']*(tiktok|instagram)", src), f
    urls = set(re.findall(r"https://([a-z0-9.-]+)", (PKG / "highlights" / "viral_data.py").read_text(encoding="utf-8")))
    assert urls <= set(viral_data.ALLOWED_HOSTS) | {"creativecommons.org"}, urls


def test_negative_windows_stay_away_from_clipped_moments():
    offs = viral_data.negative_offsets([600.0, 1800.0], 3600, 5, seed=3)
    assert len(offs) == 5 and all(abs(o - c) > 120 for o in offs for c in (600, 1800))


def test_extract_clip_stores_positive_and_negative(fresh_db, tmp_path, monkeypatch):
    import subprocess

    from clipfarm.jobs import media
    calls = []

    def fake_section(url, out_dir, name, a, b, low_res=False):
        calls.append((url, a, b, low_res))
        p = Path(out_dir) / f"{name}.mp4"
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=160x90:r=10:d={b - a:.1f}",
                        "-f", "lavfi", "-i", f"sine=f=300:d={b - a:.1f}", "-shortest", "-c:v", "libx264", "-preset",
                        "ultrafast", "-c:a", "aac", str(p)], check=True)
        return p
    monkeypatch.setattr(media, "download_section", fake_section)
    c = {"id": "Clip1", "video_id": "123", "vod_offset": 500, "duration": 20, "views": 5000, "streamer_id": 1,
         "title": "he exploded"}
    n = viral_data.extract_clip(c, 7, tmp_path, negatives=1, vod_len=3600)
    rows = db.q("SELECT source_kind, label, game_id, views FROM viral_examples ORDER BY label DESC")
    assert n == 2 and rows[0]["source_kind"] == "twitch_clip" and rows[0]["label"] == 1 and rows[0]["views"] == 5000
    assert rows[1]["label"] == 0 and rows[1]["source_kind"] in ("negative", "mundane")
    assert all(u.startswith("https://www.twitch.tv/videos/") and low for u, _, _, low in calls)
    assert not list(tmp_path.glob("*.mp4"))                 # downloads are cleaned up


def test_own_posts_and_feedback_become_examples(fresh_db):
    sid = db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                   VALUES ('twitch','9','s','S','allowed')""")
    src = db.ex("INSERT INTO sources(streamer_id, kind, platform_id, url) VALUES (?,?,?,?)", (sid, "vod", "v", "u"))
    f = {k: 0.1 for k in features.FEATURES}
    for i, (rating, views, exp) in enumerate([(1, 9000, 3000), (-1, 200, 3000)]):
        mid = db.ex("""INSERT INTO moments(source_id, start_sec, end_sec, score, signals, features, tags)
                       VALUES (?,?,?,?,'{}',?,?)""", (src, i * 100, i * 100 + 20, 0.5, json.dumps(f), json.dumps(["crash"])))
        cid = db.ex("INSERT INTO clips(moment_id, streamer_id, title, expected_views) VALUES (?,?,?,?)", (mid, sid, f"c{i}", exp))
        db.ex("INSERT INTO clip_feedback(clip_id, rating, tags) VALUES (?,?,?)", (cid, rating, "[]"))
        aid = db.ex("INSERT INTO accounts(platform, handle) VALUES ('tiktok', ?)", (f"@a{i}",))
        pid = db.ex("INSERT INTO posts(clip_id, account_id, status, scheduled_at) VALUES (?,?,'posted',datetime('now'))", (cid, aid))
        db.ex("INSERT INTO post_metrics(post_id, views) VALUES (?,?)", (pid, views))
    assert viral_data.collect_own() == 4
    lab = {(r["source_kind"], r["platform_id"]): r["label"] for r in db.q("SELECT * FROM viral_examples")}
    assert lab[("my_post", "clip1")] == 1 and lab[("my_post", "clip2")] == 0
    assert lab[("feedback", "clip1")] == 1 and lab[("feedback", "clip2")] == 0
    assert viral_data.collect_own() == 0                       # no duplicates


def test_viral_score_feeds_virality_and_earnings():
    from clipfarm.scoring import virality
    assert virality.DEFAULT_WEIGHTS["moment"] >= max(v for k, v in virality.DEFAULT_WEIGHTS.items() if k != "moment")
