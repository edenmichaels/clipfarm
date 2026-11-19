"""End-to-end smoke test on a small sample video (tests/data/sample_stream.mp4), no network.

game picked -> campaign streamer matched -> ranked -> sources fetched -> moment detected -> clip rendered
(campaign rules applied) -> reviewed -> scheduled/posted -> submission checklist -> metrics -> earnings.
Also checks a job interrupted mid-run resumes after a restart.
"""
import json
import shutil
from pathlib import Path

import pytest

from clipfarm import db
from clipfarm.campaigns import forecast, importer, matching
from clipfarm.jobs import engine as eng
from clipfarm.jobs import media, pipeline
from clipfarm.providers import SourceHit, StreamerHit

SAMPLE = Path(__file__).parent / "data" / "sample_stream.mp4"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


class FakeTwitch:
    platform = "twitch"

    def users_by_login(self, logins):
        return [StreamerHit("twitch", "777", "dylanpage", "Dylan Page", False)]

    def search_channels(self, q, n=10):
        return self.users_by_login([q])

    def clips(self, user_id, days=2, featured=None, n=20):
        if featured:
            return []
        return [SourceHit("community_clip", "clipA", "https://clips.twitch.tv/clipA", "insane chase", 4200, 12.0,
                          "2026-11-19T10:00:00Z")]

    def vods(self, user_id, n=2):
        return []


def run_jobs(kind=None, limit=20):
    """Run queued jobs inline (what the engine's worker threads do)."""
    ran = []
    e = eng.Engine(workers=1, render_workers=1)
    for _ in range(limit):
        # skip retry backoff, but leave the recurring jobs (next tick in 10 min) where they are
        db.ex("""UPDATE jobs SET run_after=datetime('now') WHERE status='queued'
                 AND kind NOT IN ('project_tick','post_due','metrics','autopilot_tick','health_check')""")
        job = e._claim("1=1" if kind is None else f"kind='{kind}'")
        if not job:
            break
        e._run(job)
        ran.append(job["kind"])
    return ran


def test_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "e2e.db")
    db.set_setting("media_dir", str(tmp_path / "media"))
    db.set_setting("transcription", {"enabled": False})
    db.set_setting("llm", {"provider": "ollama", "base_url": "http://127.0.0.1:9"})
    db.set_setting("disclaimer_accepted", True)
    fake = FakeTwitch()
    monkeypatch.setattr(pipeline, "available_providers", lambda: {"twitch": fake})
    monkeypatch.setattr(matching, "available_providers", lambda: {"twitch": fake})
    monkeypatch.setattr(pipeline, "_ensure_chat_logger", lambda s: None)

    def fake_download(url, out_dir, name):
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"{name}.mp4"
        shutil.copy(SAMPLE, dst)
        return dst
    monkeypatch.setattr(media, "download_clip", fake_download)

    # 1. game picked + campaign (seeded Dylan Page campaign, channel confirmed from the campaign page)
    gid = db.ex("INSERT INTO games(name, twitch_id) VALUES ('Grand Theft Auto VI', '1')")
    prog = db.q1("SELECT * FROM programs WHERE streamer_label='Dylan Page'")
    importer.set_fields(prog["id"], {"channel_hints": {"twitch": {"value": "dylanpage", "source": "page"}}})
    res = matching.match_program(prog["id"])
    assert res["linked"], res
    sid = res["linked"][0]["streamer_id"]
    assert db.q1("SELECT permission_status FROM streamers WHERE id=?", (sid,))["permission_status"] == "program_member"

    # 2. ranked + projected
    db.ex("INSERT INTO streamer_snapshots(streamer_id, game_id, is_live, viewers) VALUES (?,?,1,25000)", (sid, gid))
    forecast.project_all()
    from clipfarm.discovery.service import rank_game, ranked_streamers
    rank_game(gid)
    assert ranked_streamers(gid)[0]["id"] == sid

    # 3. project: tick -> fetch -> process -> render
    pid = db.ex("INSERT INTO projects(name, game_id, duration_sec, output_settings) VALUES (?,?,?,?)",
                ("e2e", gid, 3600, json.dumps({"layout": "crop", "clip": {"min_sec": 5, "max_sec": 12}})))
    db.ex("INSERT INTO project_streamers VALUES (?,?)", (pid, sid))
    eng.start_project(pid)
    ran = run_jobs()
    assert {"project_tick", "fetch_sources", "process_source", "render_moment"} <= set(ran), ran
    clip = db.q1("SELECT * FROM clips WHERE project_id=?", (pid,))
    assert clip, db.q("SELECT kind, status, substr(error,1,600) e FROM jobs WHERE error IS NOT NULL")
    assert clip and Path(clip["file_path"]).exists()
    assert clip["program_id"] == prog["id"]
    assert "#DylanPage" in clip["caption_text"]                      # campaign hashtag applied
    assert clip["layout"] in ("split", "blur_fill")                   # "must show Dylan": not an action crop

    # 4. review -> post (TikTok is manual: paste the link) -> checklist -> submit
    db.ex("UPDATE clips SET review_status='approved' WHERE id=?", (clip["id"],))
    from clipfarm.posting import submissions as subs
    tiktok = db.q1("SELECT id FROM accounts WHERE platform='tiktok'")["id"]
    sched = subs.schedule(clip["id"], [tiktok])
    assert sched[0]["manual"] is True
    out = subs.record_posted(clip["id"], tiktok, "https://www.tiktok.com/@vira1gta6clips/video/42")
    item = out["submission"]
    assert item["label"] == f"Submit to {prog['name']} on Content Rewards"
    subs.mark_submitted(item["id"])
    with pytest.raises(subs.AlreadySubmitted):
        subs.mark_submitted(item["id"])

    # 5. metrics (typed in for TikTok) + earnings
    db.ex("INSERT INTO post_metrics(post_id, views) VALUES (?, 15000)", (out["post_id"],))
    db.ex("UPDATE payouts SET status='approved', amount=15.0, amount_aud=22.95 WHERE post_id=?", (out["post_id"],))
    rows = forecast.estimated_vs_actual()
    assert rows and rows[0]["actual_aud"] == pytest.approx(22.95)


def test_job_resumes_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "r.db")
    calls = []

    @eng.handler("resumable")
    def resumable(payload, ctx):
        calls.append(payload["n"])
    jid = eng.enqueue("resumable", {"n": 1})
    db.ex("UPDATE jobs SET status='running' WHERE id=?", (jid,))      # app closed / PC rebooted mid-job
    e = eng.Engine(workers=1, render_workers=0)
    e.start()
    import time
    for _ in range(50):
        if db.q1("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "done":
            break
        time.sleep(0.1)
    e.stop()
    assert calls == [1]
    assert db.q1("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "done"
