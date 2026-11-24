"""v0.8.7: Live Watcher, unique clip-specific hooks, music off. Twitch, the LLM and Whisper are mocked."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from clipfarm import db, migrations
from clipfarm.editing import hooks
from clipfarm.editing.ffmpeg_util import ffmpeg_bin
from clipfarm.live import buffer as B, chat as C, judge as J, watcher as W
from clipfarm.llm.client import LLMUnavailable


def _no_llm(monkeypatch):
    def down(*a, **k):
        raise LLMUnavailable("Ollama isn't running")
    monkeypatch.setattr(hooks, "complete_json", down)


def _save_clip(hook: str) -> None:
    db.ex("INSERT INTO clips(title, hook_text) VALUES ('c', ?)", (hook,))


# ---- 2. same caption on every clip ----------------------------------------------------------------------------
TRANSCRIPTS = [
    "okay okay we go in the back. no way he just drove into the river! bro what",
    "I told you the bridge was safe. why is the car upside down",
    "chat look at this guy. he is wearing a traffic cone. stop stop stop",
    "we need the money now. run run run! the cops are right there",
    "who left the stove on. the whole house is on fire lmao",
    "I am the best driver in the city. oh my god I hit the only tree",
    "give me the keys. he took my car. he actually took my car!",
    "hold on hold on. the plane is going down. help me",
    "that is the worst heist in history. we got two dollars",
    "",          # no speech at all: the hook still comes from this clip's moment
]
REASONS = [None] * 9 + ["Huge explosion wipes the crew"]


def test_ten_clips_get_ten_different_hooks_without_the_llm(fresh_db, monkeypatch):
    _no_llm(monkeypatch)
    out = []
    for t, r in zip(TRANSCRIPTS, REASONS):
        pkg = hooks.generate_package(t, "Kai", "Grand Theft Auto VI", [], moment_type="Funny", reason=r)
        assert pkg["llm"] is False
        out.append(pkg["hook"])
        _save_clip(pkg["hook"])
    assert len({hooks.norm(h) for h in out}) == 10, out
    assert "Wait for it..." not in out
    for h, t in zip(out[:9], TRANSCRIPTS):                  # each hook quotes its own clip
        assert any(w in t.lower() for w in hooks.norm(h).split() if len(w) > 2), (h, t)
    assert "explosion" in out[9].lower()


def test_never_reuses_a_hook_within_the_last_50(fresh_db, monkeypatch):
    _no_llm(monkeypatch)
    seen = []
    for _ in range(12):                                      # the same transcript twelve times
        pkg = hooks.generate_package(TRANSCRIPTS[0], "Kai", "GTA VI", [], moment_type="Funny")
        seen.append(pkg["hook"])
        _save_clip(pkg["hook"])
    assert len({hooks.norm(h) for h in seen}) == 12


def test_llm_repeating_itself_is_still_unique(fresh_db, monkeypatch):
    monkeypatch.setattr(hooks, "complete_json", lambda *a, **k: {"hook": "He did NOT see that coming", "title": "t",
                                                                   "description": "d", "hashtags": ["gta6"]})
    a = hooks.generate_package(TRANSCRIPTS[1], "Kai", "GTA VI", [])
    _save_clip(a["hook"])
    b = hooks.generate_package(TRANSCRIPTS[2], "Kai", "GTA VI", [])
    assert a["hook"] == "He did NOT see that coming" and a["llm"] is True
    assert hooks.norm(b["hook"]) != hooks.norm(a["hook"]) and "cone" in b["hook"].lower() or "stop" in b["hook"].lower()


def test_hook_options_avoid_recent_hooks(fresh_db, monkeypatch):
    _no_llm(monkeypatch)
    first = hooks.generate_options(TRANSCRIPTS[3], "Kai", "GTA VI", "Funny")["hooks"]
    _save_clip(first[0])
    again = hooks.generate_options(TRANSCRIPTS[3], "Kai", "GTA VI", "Funny")["hooks"]
    assert hooks.norm(first[0]) not in {hooks.norm(h) for h in again}


# ---- 3. music off ----------------------------------------------------------------------------------------------
def test_no_auto_music_in_any_style_or_auto_edit(fresh_db):
    from clipfarm.edit import build, styles
    assert all(not s["audio"]["music"] for s in styles.BUILTINS.values())
    e = {"music": {"enabled": True, "path": "song.mp3", "manual": False}}
    styles.apply.__globals__                                  # (apply is exercised in test_v08_styles)
    code = Path(build.__file__).read_text().split("def auto_edit")[1].split("def plan_music")[0]
    assert "plan_music(" not in code and '"enabled": False' in code
    apply_src = Path(styles.__file__).read_text().split("def apply")[1]
    assert 'm["enabled"] = True' not in apply_src and e


def test_manual_music_survives_a_style_and_migration_turns_auto_music_off(tmp_path, monkeypatch):
    from clipfarm.edit import styles
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "old.db", upto=13)
    auto = {"music": {"enabled": True, "path": "a.mp3"}, "framing": "fixed"}
    manual = {"music": {"enabled": True, "path": "b.mp3", "manual": True}, "framing": "fixed"}
    for i, edl in enumerate((auto, manual), 1):
        db.ex("INSERT INTO clips(id, title) VALUES (?, 'c')", (i,))
        pid = db.ex("INSERT INTO edit_projects(clip_id, data, head) VALUES (?,?,1)", (i, json.dumps(edl)))
        db.ex("INSERT INTO edit_versions(project_id, version, data, label) VALUES (?,1,?, 'x')", (pid, json.dumps(edl)))
    st = styles.clean({"name": "Mine"})
    st["audio"]["music"] = True
    db.ex("INSERT INTO styles(name, data) VALUES ('Mine', ?)", (json.dumps(st),))
    assert 14 in migrations.run(db.connect())
    got = [json.loads(r["data"])["music"]["enabled"] for r in db.q("SELECT data FROM edit_projects ORDER BY clip_id")]
    assert got == [False, True]
    assert json.loads(db.q1("SELECT data FROM styles WHERE name='Mine'")["data"])["audio"]["music"] is False
    assert any(n["name"] == "Mine" for n in db.get_setting("style_notices"))


def test_tiktok_checklist_has_trending_step_and_sound_campaign_reminder(fresh_db):
    from clipfarm.music.trending import sound_campaigns
    from clipfarm.posting import platforms
    gta = db.q1("SELECT id FROM games WHERE name LIKE '%Grand Theft Auto%'") or \
        {"id": db.ex("INSERT INTO games(name) VALUES ('Grand Theft Auto VI')")}
    sc = sound_campaigns({"game_id": gta["id"]})
    assert sc and sc[0]["song"] == '"Tokyo Girl" by Passive' and "isn't in the video" in sc[0]["note"]
    other = db.ex("INSERT INTO games(name) VALUES ('Minecraft')")
    assert sound_campaigns({"game_id": other}) == []
    steps = platforms.checklist("tiktok", {"required_tags": [], "disclosure": None}, None, sc)
    assert any(s.startswith("Add a trending sound in TikTok before posting") for s in steps)
    assert any("Tokyo Girl" in s for s in steps)


# ---- 1. Live Watcher -----------------------------------------------------------------------------------------------
def test_chat_spike_laughs_and_clip_it_trigger():
    st = C.ChatStats()
    now = 1_000_000.0
    for i in range(300):                                    # normal: 1 message every 2 s
        st.add(now - 600 + i * 2, "nice")
    for _ in range(30):
        st.stats(now - 30)
    calm = st.score(st.stats(now - 30))
    for i in range(40):                                     # spike: 4/s, mostly laughing
        st.add(now - 10 + i * 0.25, "KEKW" if i % 2 else "clip it 😂")
    hot = st.stats(now)
    assert calm == 0 and hot["ratio"] > 3 and hot["clip_it"] >= 10 and st.score(hot) >= 0.5
    assert C.LAUGH.search("OMEGALUL") and C.LAUGH.search("💀") and not C.LAUGH.search("hello")


def test_playlist_parser_skips_twitch_ads():
    text = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:100
#EXT-X-PROGRAM-DATE-TIME:2026-11-24T10:00:00.000Z
#EXTINF:2.000,live
seg100.ts
#EXTINF:2.000,Amazon|123
ad.ts
#EXT-X-PROGRAM-DATE-TIME:2026-11-24T10:00:04.000Z
#EXTINF:2.000,live
seg102.ts
"""
    segs = B.parse_playlist(text, "https://video.example/hls/index.m3u8")
    assert [s[0] for s in segs] == [100, 102] and segs[0][2] == "https://video.example/hls/seg100.ts"
    assert segs[1][3] - segs[0][3] == 4.0


def _segments(folder: Path, n: int = 4, dur: int = 2) -> list[B.Segment]:
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={n * dur}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={n * dur}", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                    "-c:a", "aac", "-f", "hls", "-hls_time", str(dur), "-hls_list_size", "0",
                    "-hls_segment_filename", str(folder / "%010d.ts"), str(folder / "p.m3u8")], check=True)
    t0 = time.time() - n * dur
    return [B.Segment(t0 + i * dur, dur, folder / f"{i:010d}.ts") for i in range(n)]


def test_buffer_cut_stream_copies_and_trims(tmp_path):
    buf = B.LiveBuffer("kai", tmp_path / "buf", threading.Event(), analyse_audio=False)
    buf.segments = _segments(tmp_path / "buf")
    a, b = buf.span()
    s, e = buf.cut(a + 1, b - 1, tmp_path / "cut.mp4")
    assert (tmp_path / "cut.mp4").stat().st_size > 1000 and e - s == pytest.approx(8, abs=0.1)
    assert len(B.frames(tmp_path / "cut.mp4", e - s)) == 3
    buf._trim(now=b + B.KEEP_SEC - 3)                        # only the newest segments survive
    assert len(buf.segments) == 2 and not (tmp_path / "buf" / "0000000000.ts").exists()


def test_judge_prompt_rewards_funny_and_uses_my_ratings(fresh_db):
    cid = db.ex("INSERT INTO clips(title, moment_reason, hook_text) VALUES ('t', 'Streamer drives into the ocean', 'h')")
    db.ex("INSERT INTO clip_feedback(clip_id, rating) VALUES (?, 1)", (cid,))
    p = J.system_prompt()
    assert "funny, absurd, chaotic or shocking" in p and "0 for sad, serious, calm, explaining" in p
    assert 5 <= sum(1 for line in p.splitlines() if line.startswith("- 1") or line.startswith("- 9") or
                    line.startswith("- 8") or line.startswith("- 0")) and "your favourite" in p
    assert "drives into the ocean" in p


def test_judge_parses_and_falls_back_cautiously(monkeypatch):
    monkeypatch.setattr(J, "complete_json", lambda *a, **k: {"score": 9, "reason": "car flips", "start": 20, "end": 25})
    r = J.judge([{"word": "no", "start": 1, "end": 1.2}], [(1.0, "KEKW")], [], 75, "Kai", {"chat": 0.8})
    assert r["score"] == 9 and r["end"] - r["start"] >= 10 and r["llm"]

    def down(*a, **k):
        raise LLMUnavailable("off")
    monkeypatch.setattr(J, "complete_json", down)
    r = J.judge([], [], [], 75, "Kai", {"chat": 1.0, "laughter": 1.0, "viewer_clips": 1})
    assert r["llm"] is False and r["score"] <= 7.5


class FakeTwitch:
    def __init__(self, live=None, clips=None):
        self.live, self.clips = live or {}, clips or []

    def live_status(self, ids):
        return {u: s for u, s in self.live.items() if u in ids}

    def recent_clips(self, uid, since):
        return self.clips


def _streamer(allowed=True) -> int:
    if (r := db.q1("SELECT id FROM streamers WHERE platform_user_id='u1'")):
        return r["id"]
    sid = db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                   VALUES ('twitch', 'u1', 'kai', 'Kai', ?)""", ("allowed" if allowed else "unknown",))
    return sid


def test_only_permitted_streamers_are_watched_and_live_status_follows_twitch(fresh_db, monkeypatch):
    ok = _streamer()
    bad = db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                   VALUES ('twitch', 'u2', 'nope', 'Nope', 'prohibited')""")
    W.save_settings(streamers=[ok, bad])
    went = []
    monkeypatch.setattr(W.Watch, "go_live", lambda self, s: (setattr(self, "live", True), went.append("live")))
    m = W.Manager(provider_factory=lambda: FakeTwitch(live={"u1": {"title": "GTA RP"}}))
    m.step(time.time())
    assert list(m.watches) == [ok] and went == ["live"] and m.watches[ok].live
    m.provider_factory = lambda: FakeTwitch()
    m.last_poll = 0
    m.step(time.time())
    assert not m.watches[ok].live


def test_hourly_cap_but_viewer_clips_always_count(fresh_db):
    w = W.Watch({"id": 1, "login": "kai", "display_name": "Kai"})
    cfg = {**W.DEFAULTS, "per_hour": 2}
    assert w.add_trigger({"t": 1, "kind": "chat"}, cfg) and w.add_trigger({"t": 2, "kind": "chat"}, cfg)
    assert not w.add_trigger({"t": 3, "kind": "laughter"}, cfg)
    assert w.add_trigger({"t": 4, "kind": "viewer_clip"}, cfg)


def _judged(fresh, monkeypatch, score, clips=()):
    sid = _streamer()
    W.save_settings(streamers=[sid])
    m = W.Manager(provider_factory=lambda: FakeTwitch(clips=list(clips)))
    m.sync_streamers()
    w = m.watches[sid]
    w.buffer = B.LiveBuffer("kai", fresh / "media" / "live" / "kai" / "buffer", threading.Event(), analyse_audio=False)
    w.buffer.segments = _segments(fresh / "media" / "live" / "kai" / "buffer", n=35, dur=2)   # 70 s
    a, b = w.buffer.span()
    w.chat.add(a + 30, "KEKW")
    monkeypatch.setattr(W.J, "judge", lambda words, chat, imgs, length, name, sig: {
        "score": score, "reason": "car flips into the river", "start": 30.0, "end": 55.0, "type": "Funny", "llm": True})
    r = m.judge(w, {"t": b - 20, "kind": "chat", "signals": {"chat": 0.8, "laughter": 0, "scream": 0,
                                                              "viewer_clips": 0}},
                transcribe=lambda p: [{"word": "no", "start": 31.0, "end": 31.3}, {"word": "way!", "start": 31.4,
                                                                                    "end": 31.8}])
    return m, w, r, (a, b)


def test_strong_moment_confirmed_by_a_viewer_clip_becomes_a_clip(fresh_db, monkeypatch):
    jobs = []
    monkeypatch.setattr("clipfarm.jobs.engine.enqueue", lambda kind, payload, *a, **k: jobs.append((kind, payload)))
    m, w, r, (a, b) = _judged(fresh_db, monkeypatch, 6.5)
    row = db.q1("SELECT * FROM live_moments WHERE id=?", (r["id"],))
    assert row["status"] == "pending" and Path(row["path"]).exists()                # 6.5 >= threshold - 2: wait
    made = a + 52
    m.provider_factory = lambda: FakeTwitch(clips=[{"id": "c1", "created_at": W.iso(made), "duration": 20, "views": 40}])
    db.ex("UPDATE live_moments SET confirm_at=0 WHERE id=?", (r["id"],))
    m.confirm_due(time.time(), W.settings())
    row = db.q1("SELECT * FROM live_moments WHERE id=?", (r["id"],))
    assert row["status"] == "kept" and row["viewer_clips"] == 1 and row["final_score"] >= 7.5
    assert jobs and jobs[0][0] == "render_moment"
    mo = db.q1("SELECT * FROM moments WHERE id=?", (jobs[0][1]["moment_id"],))
    assert mo["start_sec"] == pytest.approx(int(made) - a - 20 - 8, abs=1.5)           # a beat before the viewer clip
    assert mo["end_sec"] == pytest.approx(min(b - a, made - a + 4), abs=1.5)
    assert Path(jobs[0][1]["media"]).exists() and db.q1("SELECT kind FROM sources")["kind"] == "live"


def test_weak_moments_are_deleted_with_the_buffer(fresh_db, monkeypatch):
    m, w, r, _ = _judged(fresh_db, monkeypatch, 2.0)
    row = db.q1("SELECT * FROM live_moments WHERE id=?", (r["id"],))
    assert row["status"] == "dropped" and not Path(row["path"]).exists()
    m2, w2, r2, _ = _judged(fresh_db, monkeypatch, 5.5)                  # pending, but nobody clipped it
    db.ex("UPDATE live_moments SET confirm_at=0 WHERE id=?", (r2["id"],))
    m2.confirm_due(time.time(), W.settings())
    row = db.q1("SELECT * FROM live_moments WHERE id=?", (r2["id"],))
    assert row["status"] == "dropped" and not Path(row["path"]).exists()


def test_status_shows_cpu_disk_and_llm_warning(fresh_db, monkeypatch):
    sid = _streamer()
    W.save_settings(streamers=[sid])
    monkeypatch.setattr(J, "llm_ok", lambda: False)
    st = W.Manager(provider_factory=FakeTwitch).status()
    assert st["streamers"][0]["display_name"] == "Kai" and "cpu_percent" in st and "disk_bytes" in st
    assert "AI model" in st["llm_warning"]


def test_one_line_transcript_still_gives_three_hook_options(fresh_db, monkeypatch):
    _no_llm(monkeypatch)
    o = hooks.generate_options("no way the cops are here bro", "Dylanpage", "GTA VI", None)["hooks"]
    assert len(o) >= 3 and "cops" in o[0]
