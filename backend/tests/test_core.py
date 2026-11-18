"""Core tests. Run: cd backend && pytest -q
No network, no API keys needed. The render tests need ffmpeg on PATH.
"""
import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from clipfarm import compliance, db
from clipfarm.discovery.ranking import RankInput, clip_profit_score
from clipfarm.editing.captions import build_ass
from clipfarm.highlights import dedupe, scorer, signals
from clipfarm.posting.scheduler import plan_slots

HAS_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "t.db")
    db.set_setting("media_dir", str(tmp_path / "media"))
    db.set_setting("transcription", {"enabled": False})
    db.set_setting("llm", {"provider": "ollama", "base_url": "http://127.0.0.1:9"})  # unreachable -> fallback
    return tmp_path


def _streamer(status="allowed", login="tester"):
    return db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                    VALUES ('twitch', ?, ?, ?, ?)""", (login + "id", login, login.title(), status))


# ---- ranking --------------------------------------------------------------------------
def test_profit_score_orders_sensibly():
    base = dict(followers=None, competition_count=5, clips_per_stream_hour=5, growth_pct=0.0)
    paid_big = clip_profit_score(RankInput(5000, payout_per_1k=2.0, permission_status="program_member", **base))
    paid_small = clip_profit_score(RankInput(50, payout_per_1k=2.0, permission_status="program_member", **base))
    unpaid = clip_profit_score(RankInput(5000, payout_per_1k=None, permission_status="allowed", **base))
    banned = clip_profit_score(RankInput(50000, payout_per_1k=5.0, permission_status="prohibited", **base))
    assert paid_big["score"] > paid_small["score"]
    assert paid_big["score"] > unpaid["score"]
    assert banned["score"] == 0 and banned["blocked"]
    assert "est." in paid_big["tier"]


def test_unknown_permission_is_ranked_down():
    kw = dict(avg_viewers=5000, followers=None, payout_per_1k=None, competition_count=None,
              clips_per_stream_hour=5, growth_pct=0)
    assert clip_profit_score(RankInput(permission_status="unknown", **kw))["score"] < \
        clip_profit_score(RankInput(permission_status="allowed", **kw))["score"]


# ---- highlight detection -------------------------------------------------------------------
def test_picks_planted_spikes():
    rng = np.random.default_rng(0)
    n = 3600
    audio = -30 + rng.normal(0, 1.5, n)
    chat = rng.poisson(2, n).astype(float)
    for t in (600, 1800, 3000):            # three planted highlights
        audio[t:t + 8] += 18
        chat[t + 2:t + 15] += 25
    curve, normed = scorer.composite({"audio": audio, "chat": chat})
    ms = scorer.pick_moments(curve, normed, threshold=0.6, top_n_per_hour=5)
    peaks = [m.peak for m in ms]
    for t in (600, 1800, 3000):
        assert any(t - 5 <= p <= t + 20 for p in peaks), (t, peaks)
    for m in ms:
        assert 15 <= m.end - m.start <= 60


def test_chat_series_counts_hype():
    rate, burst = signals.chat_series([(1.2, "KEKW KEKW"), (1.5, "OMG NO WAY!!"), (3.0, "hello")], 5)
    assert rate[1] == 2 and burst[1] >= 3 and burst[3] == 0


def test_no_signal_no_moments():
    curve, normed = scorer.composite({"audio": np.full(600, -30.0)})
    assert scorer.pick_moments(curve, normed) == []


# ---- dedupe / compliance -------------------------------------------------------------------------
def test_dedupe_adjacent_buckets(fresh_db):
    sid = _streamer()
    src = db.ex("INSERT INTO sources(streamer_id, kind, platform_id, url) VALUES (?, 'vod', 'v1', 'u')", (sid,))
    fp = dedupe.fingerprint(sid, "2026-11-18T10:00:00Z", 100, "v1")
    assert not dedupe.is_duplicate(fp)
    db.ex("INSERT INTO moments(source_id, start_sec, end_sec, score, signals, fingerprint) VALUES (?,0,1,1,'{}',?)",
          (src, fp))
    # Same moment seen 20 s later via a community clip -> duplicate
    assert dedupe.is_duplicate(dedupe.fingerprint(sid, "2026-11-18T10:00:00Z", 120, "clipX"))
    assert not dedupe.is_duplicate(dedupe.fingerprint(sid, "2026-11-18T10:00:00Z", 400, "v1"))


def test_permission_gate(fresh_db):
    ok = _streamer("program_member", "a")
    unk = _streamer("unknown", "b")
    bad = _streamer("prohibited", "c")
    assert compliance.can_clip(ok)[0]
    assert not compliance.can_clip(unk)[0]
    with pytest.raises(compliance.NotPermitted):
        compliance.assert_can_clip(bad)


# ---- captions / scheduler --------------------------------------------------------------------------
def test_ass_word_events(tmp_path):
    words = [{"word": w, "start": 10 + i * 0.5, "end": 10.4 + i * 0.5} for i, w in enumerate("one two three four".split())]
    out = build_ass(words, tmp_path / "c.ass", offset=10)
    text = out.read_text()
    assert text.count("Dialogue:") == 4
    assert "0:00:00.00" in text            # offset applied


def test_scheduler_respects_rules():
    now = datetime(2026, 11, 18, 8, 0)
    slots = plan_slots(now, [], 7, per_day=3, start_hour=9, end_hour=22, min_gap_min=90, seed=1)
    assert len(slots) == 7
    per_day = {}
    for s in slots:
        assert 8 <= s.hour <= 22 and s > now
        per_day[s.date()] = per_day.get(s.date(), 0) + 1
    assert max(per_day.values()) <= 3
    for a, b in zip(slots, slots[1:]):
        assert (b - a).total_seconds() >= 90 * 60


# ---- job engine -------------------------------------------------------------------------------------
def test_engine_runs_retries_and_recovers(fresh_db):
    from clipfarm.jobs import engine as eng
    calls = {"n": 0}

    @eng.handler("flaky")
    def flaky(payload, ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first try fails")

    jid = eng.enqueue("flaky", {})
    # simulate a crash mid-job: a 'running' job left behind
    stuck = eng.enqueue("flaky", {})
    db.ex("UPDATE jobs SET status='running' WHERE id=?", (stuck,))
    e = eng.Engine(workers=1, render_workers=0)
    e.start()                                          # recovery puts 'stuck' back in the queue
    deadline = time.time() + 10
    while time.time() < deadline:
        db.ex("UPDATE jobs SET run_after=datetime('now') WHERE status='queued'")  # skip backoff in test
        rows = db.q("SELECT status FROM jobs WHERE id IN (?,?)", (jid, stuck))
        if all(r["status"] == "done" for r in rows):
            break
        time.sleep(0.2)
    e.stop()
    assert all(r["status"] == "done" for r in db.q("SELECT status FROM jobs WHERE id IN (?,?)", (jid, stuck)))


# ---- render + full render_moment handler ------------------------------------------------------------
@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    p = tmp_path_factory.mktemp("v") / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=25",
                    "-f", "lavfi", "-i", "sine=f=330:d=25", "-shortest", "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", str(p)], check=True)
    return p


@pytest.mark.parametrize("layout", ["crop", "blur_fill", "auto"])
def test_render_layouts(sample_video, tmp_path, layout):
    from clipfarm.editing.ffmpeg_util import probe
    from clipfarm.editing.render import RenderSpec, render
    words = [{"word": w, "start": 4 + i * 0.4, "end": 4.35 + i * 0.4} for i, w in enumerate("no way that worked".split())]
    r = render(RenderSpec(sample_video, 3, 19, tmp_path / f"{layout}.mp4", layout=layout, words=words,
                          hook="He actually did it", credit="twitch.tv/tester", quality="fast"))
    info = probe(Path(r["path"]))
    assert (info["width"], info["height"]) == (1080, 1920)
    assert 15.5 <= info["duration"] <= 16.5
    assert info["has_audio"] and r["thumb"] and len(r["sha256"]) == 64


def test_render_moment_pipeline(fresh_db, sample_video):
    from clipfarm.jobs import pipeline
    from clipfarm.jobs.engine import JobContext, enqueue
    sid = _streamer("program_member")
    gid = db.ex("INSERT INTO games(name) VALUES ('Grand Theft Auto VI')")
    prog = db.ex("""INSERT INTO programs(name, operator, payout_per_1k, required_tags, required_credit, disclosure)
                    VALUES ('Test Program','whop',1.5,'["gta6clips"]','Credit: {url}','#partner')""")
    db.ex("INSERT INTO streamer_programs VALUES (?,?)", (sid, prog))
    db.set_setting("disclaimer_accepted", True)
    pid = db.ex("INSERT INTO projects(name, game_id, duration_sec, output_settings, status) VALUES (?,?,?,?, 'running')",
                ("t", gid, 3600, json.dumps({"layout": "blur_fill"})))
    src = db.ex("""INSERT INTO sources(streamer_id, kind, platform_id, url, local_path, status)
                   VALUES (?, 'community_clip', 'c1', 'u', ?, 'ready')""", (sid, str(sample_video)))
    mid = db.ex("INSERT INTO moments(source_id, start_sec, end_sec, score, signals, status) VALUES (?,2,20,0.9,'{}','queued')",
                (src,))
    jid = enqueue("render_moment", {"project_id": pid, "moment_id": mid, "media": str(sample_video), "media_offset": 0})
    pipeline.render_moment({"project_id": pid, "moment_id": mid, "media": str(sample_video), "media_offset": 0},
                           JobContext(db.q1("SELECT * FROM jobs WHERE id=?", (jid,))))
    c = db.q1("SELECT * FROM clips WHERE moment_id=?", (mid,))
    assert c and Path(c["file_path"]).exists()
    assert c["program_id"] == prog
    assert "gta6clips" in json.loads(c["hashtags"])
    assert "#partner" in c["description"] and "twitch.tv/tester" in c["description"]
    out = pipeline.export_clip(c["id"], fresh_db / "exports")
    assert Path(out["video"]).exists() and "gta6clips" in Path(out["caption_file"]).read_text()


def test_render_moment_blocks_unpermitted(fresh_db, sample_video):
    from clipfarm.jobs import pipeline
    sid = _streamer("unknown", "nope")
    pid = db.ex("INSERT INTO projects(name, duration_sec, output_settings) VALUES ('t', 60, '{}')")
    src = db.ex("INSERT INTO sources(streamer_id, kind, platform_id, url) VALUES (?, 'vod', 'v', 'u')", (sid,))
    mid = db.ex("INSERT INTO moments(source_id, start_sec, end_sec, score, signals) VALUES (?,0,20,1,'{}')", (src,))
    with pytest.raises(compliance.NotPermitted):
        pipeline.render_moment({"project_id": pid, "moment_id": mid, "media": str(sample_video)}, None)
