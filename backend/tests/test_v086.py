"""v0.8.6: My Clips (sort, select, delete, Recently deleted) and posting (YouTube quota, TikTok inbox, Instagram
resumable upload, X, Post Kit, Send to phone, one posting screen). Every platform API is mocked: nothing here
talks to the internet."""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipfarm import db, library, migrations, secrets
from clipfarm.posting import common, instagram, phone, platforms, qr, submissions as subs, tiktok, x, youtube


# ---- helpers ---------------------------------------------------------------------------------------------------
class Resp:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code, self.headers = status, headers or {}
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.content = self.text.encode()
        self.ok = status < 400

    def json(self):
        return self._body if self._body is not None else json.loads(self.text or "{}")


class FakeHTTP:
    """Routes (method, url-substring) to a function(kwargs) -> Resp, and records every call."""
    def __init__(self):
        self.routes, self.calls = [], []

    def on(self, method, part, fn):
        self.routes.append((method, part, fn))

    def _do(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for m, part, fn in reversed(self.routes):
            if m == method and part in url:
                return fn(url=url, **kw)
        raise AssertionError(f"unexpected {method} {url}")

    def ns(self):
        return SimpleNamespace(get=lambda url, **kw: self._do("GET", url, **kw),
                               post=lambda url, **kw: self._do("POST", url, **kw),
                               put=lambda url, **kw: self._do("PUT", url, **kw), Response=Resp)


def _clip(tmp: Path, title="He did NOT see that coming", size=3_000_000, program_id=None, virality=50, **cols) -> int:
    media = tmp / "media" / "clips"
    media.mkdir(parents=True, exist_ok=True)
    f = media / f"clip_{title[:6].replace(' ', '_')}_{virality}_{time.time_ns()}.mp4"
    f.write_bytes(b"\0" * size)
    th = f.with_suffix(".jpg")
    th.write_bytes(b"\xff" * 2000)
    cid = db.ex("""INSERT INTO clips(title, description, hashtags, file_path, thumb_path, duration_sec, review_status,
                   program_id, virality, profit_likely) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (title, "Clip of the day", json.dumps(["gta6", "funny"]), str(f), str(th), cols.get("dur", 30),
                 cols.get("review", "approved"), program_id, virality, cols.get("aud")))
    return cid


def _connect(aid: int, token: dict) -> None:
    secrets.set_secret(f"account:{aid}", token)
    db.ex("UPDATE accounts SET oauth_status='connected', external_id=COALESCE(external_id, ?) WHERE id=?", (f"ext{aid}", aid))


def _acct(platform: str) -> dict:
    return db.q1("SELECT * FROM accounts WHERE platform=?", (platform,))


# ---- 1. My Clips: sort, delete, Recently deleted ------------------------------------------------------------------
def test_sort_by_viral_score_by_default_and_remembered(fresh_db):
    a = _clip(fresh_db, "low", virality=20, aud=9.0, dur=50)
    b = _clip(fresh_db, "high", virality=90, aud=1.0, dur=10)
    c = _clip(fresh_db, "mid", virality=60, aud=5.0, dur=30)
    assert [r["id"] for r in library.list_clips(sort="")] == [b, c, a]                  # default: viral score, highest first
    assert [r["id"] for r in library.list_clips(sort="projected")] == [a, c, b]
    assert db.get_setting("myclips.sort") == "projected"                         # remembered
    assert [r["id"] for r in library.list_clips(sort="")] == [a, c, b]
    assert [r["id"] for r in library.list_clips(sort="length")] == [a, c, b]
    assert {r["status_name"] for r in library.list_clips(sort="status")} == {"not posted"}
    assert library.sort_key(None) == "status"


def test_select_all_delete_restore(fresh_db):
    ids = [_clip(fresh_db, f"clip {i}", virality=i) for i in range(5)]
    pv = library.preview(ids)
    assert pv["count"] == 5 and pv["message"].startswith("Delete 5 clips? This frees ")
    assert pv["bytes"] >= 5 * 3_000_000 and "MB" in pv["size"]
    r = library.delete(ids)
    assert r["deleted"] == 5
    assert library.list_clips(sort="") == []                                              # hidden from My Clips
    tr = library.trash()
    assert len(tr) == 5 and all(t["days_left"] == 7 for t in tr)
    assert all(Path(db.q1("SELECT file_path FROM clips WHERE id=?", (i,))["file_path"]).exists() for i in ids)
    assert library.restore(ids)["restored"] == 5
    assert len(library.list_clips(sort="")) == 5 and library.trash() == []


def test_empty_now_frees_disk_and_leaves_no_orphans(fresh_db):
    ids = [_clip(fresh_db, f"c{i}") for i in range(3)]
    files = [Path(db.q1("SELECT file_path FROM clips WHERE id=?", (i,))["file_path"]) for i in ids]
    cache = fresh_db / "media" / "edit_cache" / f"c{ids[0]}"
    cache.mkdir(parents=True)
    (cache / "base.mp4").write_bytes(b"1" * 100_000)
    proxy = fresh_db / "media" / "proxy.mp4"
    proxy.write_bytes(b"2" * 50_000)
    db.set_setting(f"proxy:{ids[0]}", {"path": str(proxy), "head": 1})
    db.ex("INSERT INTO edit_projects(clip_id, data) VALUES (?, '{}')", (ids[0],))
    before = sum(f.stat().st_size for f in (fresh_db / "media").rglob("*") if f.is_file())
    expected = library.preview(ids)["bytes"]
    library.delete(ids)
    assert library.purge()["purged"] == 0                                        # not expired yet
    out = library.purge(expired_only=False)                                      # "Empty now"
    after = sum(f.stat().st_size for f in (fresh_db / "media").rglob("*") if f.is_file())
    assert out["purged"] == 3 and out["freed"] == expected == before - after
    assert not any(f.exists() for f in files) and not cache.exists() and not proxy.exists()
    assert not list((fresh_db / "media").rglob("*.mp4")) and not list((fresh_db / "media").rglob("*.jpg"))
    assert db.get_setting(f"proxy:{ids[0]}") is None
    assert not db.q1("SELECT 1 FROM edit_projects WHERE clip_id=?", (ids[0],))
    assert library.trash() == [] and library.restore(ids)["restored"] == 0       # gone for good


def test_expired_clips_purged_by_storage_job(fresh_db):
    cid = _clip(fresh_db)
    library.delete([cid])
    db.ex("UPDATE clips SET purge_after=datetime('now','-1 minute') WHERE id=?", (cid,))
    from clipfarm.jobs import storage
    storage.cleanup()
    assert db.q1("SELECT purged_at, file_path FROM clips WHERE id=?", (cid,))["file_path"] is None


def test_posted_clip_stats_kept_after_delete(fresh_db):
    prog = db.q1("SELECT id FROM programs WHERE allowed_platforms='[]' LIMIT 1")["id"]
    cid = _clip(fresh_db, program_id=prog)
    tt = _acct("tiktok")["id"]
    res = subs.record_posted(cid, tt, "https://www.tiktok.com/@vira1gta6clips/video/1")
    db.ex("INSERT INTO post_metrics(post_id, views) VALUES (?, 12345)", (res["post_id"],))
    subs.mark_submitted(res["submission"]["id"])
    pv = library.preview([cid])
    assert pv["posted"] == 1 and "1 of these was posted; its stats are kept." in pv["notes"]
    library.delete([cid])
    library.purge(expired_only=False)
    p = db.q1("SELECT * FROM posts WHERE id=?", (res["post_id"],))
    assert p["url"].endswith("/video/1") and p["status"] == "posted"
    assert db.q1("SELECT views FROM post_metrics WHERE post_id=?", (p["id"],))["views"] == 12345
    assert db.q1("SELECT status FROM submissions WHERE post_id=?", (p["id"],))["status"] == "done"
    assert db.q1("SELECT 1 FROM payouts WHERE post_id=?", (p["id"],))                       # Earnings still sees it
    assert db.q1("SELECT program_id FROM clips WHERE id=?", (cid,))["program_id"] == prog  # campaign kept


def test_shared_source_kept_until_no_clip_uses_it(fresh_db):
    src = fresh_db / "media" / "sources" / "dom_m7.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"s" * 10_000)
    a, b = _clip(fresh_db, "a"), _clip(fresh_db, "b")
    db.ex("UPDATE clips SET source_path=? WHERE id IN (?,?)", (str(src), a, b))
    library.delete([a])
    library.purge(expired_only=False)
    from clipfarm.jobs import storage
    storage.cleanup()
    assert src.exists()                                                          # b still uses it
    library.delete([b])
    library.purge(expired_only=False)
    storage.cleanup()
    assert not src.exists()                                                      # storage job cleaned it up


def test_delete_while_rendering_or_uploading_cancels_first(fresh_db):
    cid = _clip(fresh_db)
    jid = db.ex("INSERT INTO jobs(kind, payload, status) VALUES ('export_edit', ?, 'running')", (json.dumps({"clip_id": cid}),))
    yt = _acct("youtube")["id"]
    pid = db.ex("INSERT INTO posts(clip_id, account_id, scheduled_at, status, manual, stage) VALUES (?,?,datetime('now'),"
                "'uploading',0,'auto')", (cid, yt))
    assert library.preview([cid])["busy"] == [cid]
    r = library.delete([cid])
    assert r["cancelled_jobs"] == 1 and r["cancelled_posts"] == 1
    assert db.q1("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "cancelled"
    with pytest.raises(common.UploadCancelled):                                  # the uploader stops at its next chunk
        list(common.chunks(Path(db.q1("SELECT file_path FROM clips WHERE id=?", (cid,))["file_path"]), 1024, pid))
    db.ex("UPDATE jobs SET status='running' WHERE id=?", (jid,))                 # render still finishing:
    assert library.purge(expired_only=False)["skipped"] == 1                     # files left until it stops


def test_migration_v13_moves_old_deletes_into_recently_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "old.db", upto=12)
    db.ex("INSERT INTO clips(title, deleted_at) VALUES ('old', datetime('now','-2 days'))")
    applied = migrations.run(db.connect())
    assert 13 in applied and migrations.LATEST >= 13
    assert db.q1("SELECT purge_after FROM clips WHERE title='old'")["purge_after"]
    cols = {r[1] for r in db.connect().execute("PRAGMA table_info(posts)")}
    assert {"stage", "publish_id", "sent_at"} <= cols


# ---- 2. YouTube ---------------------------------------------------------------------------------------------------
def _yt(monkeypatch, fresh, quota_error=False):
    http = FakeHTTP()
    put_ranges = []

    def init(url, **kw):
        if quota_error:
            return Resp(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}})
        meta = json.loads(kw["data"])
        http.meta = meta
        return Resp(200, {}, headers={"Location": "https://upload.example/session1"})

    def put(url, **kw):
        put_ranges.append(kw["headers"]["Content-Range"])
        total = int(kw["headers"]["Content-Range"].split("/")[1])
        end = int(kw["headers"]["Content-Range"].split("-")[1].split("/")[0])
        return Resp(200, {"id": "vid123"}) if end == total - 1 else Resp(308, text="")
    http.on("POST", "/upload/youtube/v3/videos", init)
    http.on("PUT", "upload.example/session1", put)
    monkeypatch.setattr(youtube, "requests", http.ns())
    yt = _acct("youtube")["id"]
    _connect(yt, {"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 3600,
                  "connected_at": time.time()})
    return http, put_ranges, yt


def test_youtube_upload_with_disclosure_shorts_and_link_to_checklist(fresh_db, monkeypatch):
    prog = db.q1("SELECT id FROM programs WHERE allowed_platforms='[]' LIMIT 1")["id"]
    db.ex("UPDATE programs SET required_tags=?, disclosure=? WHERE id=?", (json.dumps(["#DylanPage"]), "#ad", prog))
    http, ranges, yt = _yt(monkeypatch, fresh_db)
    cid = _clip(fresh_db, program_id=prog, size=20 * 1024 * 1024)
    p = subs.schedule(cid, [yt])[0]
    assert p["stage"] == "auto" and not p["manual"]
    db.ex("UPDATE posts SET scheduled_at=datetime('now','localtime','-1 minute') WHERE id=?", (p["post_id"],))
    from clipfarm.jobs import pipeline
    monkeypatch.setattr(pipeline, "check_tiktok_drafts", lambda: 0)
    pipeline.post_due({}, SimpleNamespace(log=lambda *a, **k: None))
    post = db.q1("SELECT * FROM posts WHERE id=?", (p["post_id"],))
    assert post["status"] == "posted" and post["url"] == "https://youtube.com/shorts/vid123" and post["sent_at"]
    assert len(ranges) == 3 and ranges[0] == f"bytes 0-{8 * 1024 * 1024 - 1}/{20 * 1024 * 1024}"   # 8 MB chunks
    m = http.meta
    assert m["snippet"]["title"].endswith("#Shorts") and m["status"]["selfDeclaredMadeForKids"] is False
    assert "#DylanPage" in m["snippet"]["description"] and "#ad" in m["snippet"]["description"]
    assert "#Shorts" in m["snippet"]["description"]
    assert subs.checklist()[0]["post_url"] == "https://youtube.com/shorts/vid123"      # fills the submission item


def test_youtube_quota_shown_and_extras_queued_for_next_day(fresh_db, monkeypatch):
    http, _, yt = _yt(monkeypatch, fresh_db)
    db.set_setting("posting", {"youtube_daily_uploads": 2, "posts_per_day_per_account": 50, "min_gap_minutes": 1})
    q = youtube.quota()
    assert q["label"] == "2 uploads left today"
    ids = [_clip(fresh_db, f"q{i}") for i in range(3)]
    from clipfarm.jobs import pipeline
    monkeypatch.setattr(pipeline, "check_tiktok_drafts", lambda: 0)
    for cid in ids:
        db.ex("""INSERT INTO posts(clip_id, account_id, scheduled_at, status, manual, stage)
                 VALUES (?,?,datetime('now','localtime','-1 minute'),'scheduled',0,'auto')""", (cid, yt))
    pipeline.post_due({}, SimpleNamespace(log=lambda *a, **k: None))
    rows = db.q("SELECT * FROM posts WHERE account_id=? ORDER BY id", (yt,))
    assert [r["status"] for r in rows] == ["posted", "posted", "scheduled"]
    assert youtube.quota()["label"] == "0 uploads left today"
    _, reset = youtube.quota_day()
    later = datetime.fromisoformat(rows[2]["scheduled_at"])
    assert later >= reset.astimezone().replace(tzinfo=None) and rows[2]["error"].startswith("Queued:")


def test_youtube_quota_exceeded_from_google_waits(fresh_db, monkeypatch):
    _, _, yt = _yt(monkeypatch, fresh_db, quota_error=True)
    cid = _clip(fresh_db)
    with pytest.raises(youtube.QuotaUsed):
        youtube.upload(yt, Path(db.q1("SELECT file_path FROM clips WHERE id=?", (cid,))["file_path"]), "t", "d", [])
    assert youtube.quota()["left"] == 0


def test_youtube_testing_mode_reminder(fresh_db):
    yt = _acct("youtube")["id"]
    _connect(yt, {"refresh_token": "r", "refresh_expires_at": time.time() + 3 * 86400})
    assert "Testing" in youtube.consent_reminder(yt)
    _connect(yt, {"refresh_token": "r", "connected_at": time.time() - 5 * 86400})
    assert "stops working" in youtube.consent_reminder(yt)
    db.set_setting("google_consent_published", True)
    assert youtube.consent_reminder(yt) is None


# ---- 3. TikTok inbox --------------------------------------------------------------------------------------------
def _tt(monkeypatch, status="SEND_TO_USER_INBOX", post_id=None, pending_error=False):
    http = FakeHTTP()
    sent = []

    def init(url, **kw):
        if pending_error:
            return Resp(403, {"error": {"code": "spam_risk_too_many_pending_share", "message": "too many"}})
        si = kw["json"]["source_info"]
        http.init = si
        return Resp(200, {"data": {"publish_id": f"pub{len(sent)}", "upload_url": "https://up.tiktok/x"},
                          "error": {"code": "ok"}})
    http.on("POST", "/inbox/video/init/", init)
    http.on("PUT", "up.tiktok", lambda url, **kw: sent.append(kw["headers"]["Content-Range"]) or Resp(201, {}))
    http.on("POST", "/status/fetch/", lambda url, **kw: Resp(200, {"data": {
        "status": status, "publicaly_available_post_id": [post_id] if post_id else []}}))
    monkeypatch.setattr(tiktok, "requests", http.ns())
    aid = _acct("tiktok")["id"]
    _connect(aid, {"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 3600, "scope": "video.upload"})
    return http, sent, aid


def test_tiktok_inbox_upload_and_five_a_day_queue(fresh_db, monkeypatch):
    http, sent, aid = _tt(monkeypatch)
    assert platforms.mode(_acct("tiktok")) == "inbox"
    path = Path(db.q1("SELECT file_path FROM clips WHERE id=?", (_clip(fresh_db, size=23 * 1024 * 1024),))["file_path"])
    r = tiktok.send_to_inbox(aid, path)
    assert r["publish_id"] == "pub0"
    assert http.init == {"source": "FILE_UPLOAD", "video_size": 23 * 1024 * 1024, "chunk_size": 10 * 1024 * 1024,
                         "total_chunk_count": 2}
    assert sent == [f"bytes 0-{10 * 1024 * 1024 - 1}/{23 * 1024 * 1024}",
                    f"bytes {10 * 1024 * 1024}-{23 * 1024 * 1024 - 1}/{23 * 1024 * 1024}"]   # last chunk takes the rest
    from clipfarm.jobs import pipeline
    ids = [_clip(fresh_db, f"t{i}") for i in range(6)]
    for cid in ids:
        db.ex("""INSERT INTO posts(clip_id, account_id, scheduled_at, status, manual, stage)
                 VALUES (?,?,datetime('now','localtime','-1 minute'),'scheduled',0,'inbox')""", (cid, aid))
    pipeline.post_due({}, SimpleNamespace(log=lambda *a, **k: None))
    rows = db.q("SELECT * FROM posts WHERE account_id=? ORDER BY id", (aid,))
    inbox = [r for r in rows if r["sent_at"]]
    assert len(inbox) == 5 and tiktok.drafts(aid)["label"] == "5 of 5 TikTok drafts used today"
    waiting = [r for r in rows if not r["sent_at"]]
    assert len(waiting) == 1 and waiting[0]["error"].startswith("Queued:")
    assert datetime.fromisoformat(waiting[0]["scheduled_at"]) > datetime.now() + timedelta(hours=20)
    st = platforms.status_of({**inbox[0]}, _acct("tiktok"), {"program_id": None})
    assert st["label"] == "In TikTok inbox"


def test_tiktok_status_fills_link_or_asks_for_it(fresh_db, monkeypatch):
    from clipfarm.jobs import pipeline
    _, _, aid = _tt(monkeypatch, status="PUBLISH_COMPLETE", post_id="7300000000000000001")
    cid = _clip(fresh_db)
    pid = db.ex("""INSERT INTO posts(clip_id, account_id, scheduled_at, status, manual, stage, publish_id, sent_at)
                   VALUES (?,?,datetime('now'),'scheduled',0,'inbox','pub9',datetime('now'))""", (cid, aid))
    assert pipeline.check_tiktok_drafts() == 1
    p = db.q1("SELECT * FROM posts WHERE id=?", (pid,))
    assert p["status"] == "posted" and p["url"] == "https://www.tiktok.com/@vira1gta6clips/video/7300000000000000001"
    _tt(monkeypatch, status="PUBLISH_COMPLETE", post_id=None)
    cid2 = _clip(fresh_db, "b")
    pid2 = db.ex("""INSERT INTO posts(clip_id, account_id, scheduled_at, status, manual, stage, publish_id, sent_at)
                    VALUES (?,?,datetime('now'),'scheduled',0,'inbox','pub10',datetime('now'))""", (cid2, aid))
    pipeline.check_tiktok_drafts()
    p2 = db.q1("SELECT * FROM posts WHERE id=?", (pid2,))
    assert p2["stage"] == "inbox_posted"
    assert platforms.status_of(p2, _acct("tiktok"), {})["code"] == "inbox_posted"     # "paste the link"
    subs.record_posted(cid2, aid, "https://www.tiktok.com/@vira1gta6clips/video/2")
    assert db.q1("SELECT status FROM posts WHERE id=?", (pid2,))["status"] == "posted"


def test_tiktok_pending_limit_from_api_is_queued_not_failed(fresh_db, monkeypatch):
    _, _, aid = _tt(monkeypatch, pending_error=True)
    path = Path(db.q1("SELECT file_path FROM clips WHERE id=?", (_clip(fresh_db),))["file_path"])
    with pytest.raises(tiktok.DraftsFull):
        tiktok.send_to_inbox(aid, path)


def test_tiktok_direct_post_kept_but_off(fresh_db):
    assert not tiktok.direct_enabled()
    with pytest.raises(common.NeedsUser):
        tiktok.direct_post(_acct("tiktok")["id"], Path("x.mp4"), "caption")
    url = (secrets.set_secret("tiktok_client_key", "ck") or tiktok.auth_url())
    assert "video.upload" in url and "video.publish" not in url and "code_challenge_method=S256" in url


# ---- 4. Instagram ------------------------------------------------------------------------------------------------
def _ig(monkeypatch, linked=True, perms=("instagram_basic", "instagram_content_publish", "pages_show_list"),
        quota_used=3):
    http = FakeHTTP()
    http.on("GET", "/me/permissions", lambda url, **kw: Resp(200, {"data": [{"permission": p, "status": "granted"}
                                                                          for p in perms]}))
    http.on("GET", "/me/accounts", lambda url, **kw: Resp(200, {"data": [
        {"name": "GTA6 Viral Clips", **({"instagram_business_account": {"id": "1784", "username": "gta6.viralclipsss"}}
                                       if linked else {})}]}))
    http.on("GET", "/oauth/access_token", lambda url, **kw: Resp(200, {"access_token": "LONG" * 12, "expires_in": 5184000}))
    http.on("GET", "/content_publishing_limit", lambda url, **kw: Resp(200, {"data": [
        {"quota_usage": quota_used, "config": {"quota_total": 100}}]}))
    states = iter(["IN_PROGRESS", "FINISHED"])
    http.on("POST", "/1784/media", lambda url, **kw: setattr(http, "container", kw["data"]) or
            Resp(200, {"id": "cont1", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/cont1"}))
    http.on("POST", "/1784/media_publish", lambda url, **kw: Resp(200, {"id": "media55"}))
    http.on("POST", "rupload.facebook.com", lambda url, **kw: setattr(http, "upload", kw["headers"]) or
            Resp(200, {"success": True}))
    http.on("GET", "/cont1", lambda url, **kw: Resp(200, {"status_code": next(states)}))
    http.on("GET", "/media55", lambda url, **kw: Resp(200, {"permalink": "https://www.instagram.com/reel/ABC/"}))
    monkeypatch.setattr(instagram, "requests", http.ns())
    secrets.set_secret("meta_app_id", "app")
    secrets.set_secret("meta_app_secret", "sec")
    return http


def test_instagram_eligibility_failure_shows_steps_and_falls_back_to_post_kit(fresh_db, monkeypatch):
    _ig(monkeypatch, linked=False)
    chk = instagram.connect("https://www.facebook.com/connect/login_success.html#access_token=" + "S" * 60 + "&expires_in=3600")
    assert not chk["ok"] and chk["steps"][0]["title"] == "Switch to a professional account"
    assert any("isn't linked" in p for p in chk["problems"])
    assert platforms.mode(_acct("instagram")) == "kit"
    cid = _clip(fresh_db)
    assert subs.schedule(cid, [_acct("instagram")["id"]])[0]["stage"] == "kit"


def test_instagram_resumable_upload_and_publish(fresh_db, monkeypatch):
    http = _ig(monkeypatch)
    chk = instagram.connect("S" * 60)
    assert chk["ok"] and chk["username"] == "gta6.viralclipsss" and chk["limit"]["left"] == 97
    acct = _acct("instagram")
    assert acct["external_id"] == "1784" and platforms.mode(acct) == "auto"
    cid = _clip(fresh_db, size=1234)
    clip = db.q1("SELECT * FROM clips WHERE id=?", (cid,))
    r = instagram.publish(acct["id"], Path(clip["file_path"]), "caption #gta6", poll_every=0)
    assert r == {"external_id": "media55", "url": "https://www.instagram.com/reel/ABC/"}
    assert http.container["media_type"] == "REELS" and http.container["upload_type"] == "resumable"
    assert http.upload["offset"] == "0" and http.upload["file_size"] == "1234"
    assert http.upload["Authorization"].startswith("OAuth ")


def test_instagram_publishing_limit_checked_first(fresh_db, monkeypatch):
    _ig(monkeypatch, quota_used=100)
    instagram.connect("S" * 60)
    cid = _clip(fresh_db)
    with pytest.raises(common.NeedsUser, match="publishing limit"):
        instagram.publish(_acct("instagram")["id"], Path(db.q1("SELECT file_path FROM clips WHERE id=?", (cid,))["file_path"]), "c")


# ---- 5. X -------------------------------------------------------------------------------------------------------
def test_x_oauth1_signature_matches_the_reference_example():
    keys = {"x_api_key": "xvz1evFS4wEEPTGEFPHBog", "x_api_secret": "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
            "x_access_token": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
            "x_access_secret": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"}
    h = x.oauth_header("POST", "https://api.twitter.com/1.1/statuses/update.json",
                       {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
                       nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", ts="1318622958", keys=keys)
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in h


def test_x_without_keys_uses_post_kit(fresh_db):
    assert not x.configured()
    assert platforms.mode({"id": 99, "platform": "x", "oauth_status": None}) == "kit"


# ---- 6. Post Kit ---------------------------------------------------------------------------------------------------
def test_post_kit_caption_page_and_file(fresh_db, monkeypatch):
    prog = db.q1("SELECT id FROM programs WHERE allowed_platforms='[]' LIMIT 1")["id"]
    db.ex("UPDATE programs SET required_tags=? WHERE id=?", (json.dumps(["#DylanPage"]), prog))
    cid = _clip(fresh_db, program_id=prog)
    ig = _acct("instagram")["id"]
    p = subs.schedule(cid, [ig])[0]
    k = platforms.kit(p["post_id"])
    assert k["upload_url"] == "https://www.instagram.com/" and k["platform_name"] == "Instagram"
    assert "#DylanPage" in k["caption"] and "#gta6" in k["caption"] and k["caption"].startswith("He did NOT")
    assert "Check the campaign hashtag #DylanPage is in the caption" in k["checklist"]
    assert k["checklist"][-1] == "Copy the post's link and paste it below"
    opened = []
    monkeypatch.setattr(platforms.proc, "popen", lambda cmd, **kw: opened.append(cmd))
    monkeypatch.setattr(platforms.sys, "platform", "win32")
    platforms.reveal(k["file"])
    assert opened == [["explorer", f"/select,{Path(k['file'])}"]]                 # the right file, selected
    tt = subs.schedule(cid, [_acct("tiktok")["id"]])[0]
    assert platforms.kit(tt["post_id"])["upload_url"] == "https://www.tiktok.com/tiktokstudio/upload"


# ---- 7. Send to phone ------------------------------------------------------------------------------------------------
def test_send_to_phone_token_expiry_and_network(fresh_db):
    cid = _clip(fresh_db, size=50_000)
    f = db.q1("SELECT file_path FROM clips WHERE id=?", (cid,))["file_path"]
    link = phone.create(cid, Path(f), "Caption #gta6", "Title")
    assert link["url"].startswith("http://") and "/p/" in link["url"] and len(link["token"]) >= 32
    assert link["qr_svg"].startswith("<svg")
    port = phone.start()
    base = f"http://127.0.0.1:{port}"
    page = urllib.request.urlopen(f"{base}/p/{link['token']}").read().decode()
    assert "Copy caption" in page and "Caption #gta6" in page
    data = urllib.request.urlopen(f"{base}/p/{link['token']}/video").read()
    assert len(data) == 50_000
    for bad in ("/p/wrongtoken", "/p/", "/"):                                    # token required
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + bad)
        assert e.value.code in (404, 410)
    assert phone.get_link(link["token"], now=time.time() + 3601) is None        # expires after an hour
    phone._links[link["token"]]["expires"] = time.time() - 1
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"{base}/p/{link['token']}")
    assert e.value.code == 410
    assert phone.allowed("192.168.1.50", "192.168.1.20")                         # same home network
    assert not phone.allowed("192.168.7.50", "192.168.1.20")                     # another private network
    assert not phone.allowed("8.8.8.8", "192.168.1.20")                          # the internet
    assert not phone.allowed("100.64.1.1", "192.168.1.20")
    assert phone.allowed("127.0.0.1", "192.168.1.20")                            # this PC
    phone.stop()


def test_sync_folder_export(fresh_db):
    cid = _clip(fresh_db, size=1000)
    f = Path(db.q1("SELECT file_path FROM clips WHERE id=?", (cid,))["file_path"])
    dest = phone.to_sync_folder(f, "my caption", str(fresh_db / "Dropbox" / "ClipFarm"))
    assert dest.exists() and dest.with_suffix(".txt").read_text() == "my caption"


def test_qr_code_decodes():
    import cv2
    url = "http://192.168.1.23:8766/p/" + "Ab3_" * 8
    val, _, _ = cv2.QRCodeDetector().detectAndDecode(qr.png(url))
    assert val == url


# ---- 8. One posting screen, never twice ---------------------------------------------------------------------------------
def test_post_all_and_never_twice_to_the_same_account(fresh_db, monkeypatch):
    _yt(monkeypatch, fresh_db)
    _tt(monkeypatch)
    db.set_setting("posting", {"posts_per_day_per_account": 20, "min_gap_minutes": 5})
    ids = [_clip(fresh_db, f"ready {i}", virality=80 - i) for i in range(2)]
    r = platforms.post_all()
    assert r["auto"] == 2 and r["inbox"] == 2 and r["kit"] == 2                 # YouTube auto, TikTok inbox, IG kit
    assert "6 posts scheduled" in r["message"]
    assert platforms.post_all()["scheduled"] == 0                                # nothing scheduled twice
    with pytest.raises(subs.PostingBlocked):
        subs.schedule(ids[0], [_acct("youtube")["id"]])
    with pytest.raises(subs.PostingBlocked):                                     # same video, other clip row
        dup = _clip(fresh_db, "dup")
        h = "hash1"
        db.ex("UPDATE clips SET content_hash=? WHERE id IN (?,?)", (h, ids[0], dup))
        subs.schedule(dup, [_acct("youtube")["id"]])
    ov = platforms.overview()
    labels = {s["label"].split(" at ")[0] for row in ov["clips"] for s in row["statuses"]}
    assert {"Auto", "Inbox", "Post Kit needed"} <= labels
    cards = {c["platform"]: c for c in ov["accounts"]}
    assert cards["youtube"]["quota"]["label"].endswith("uploads left today")
    assert cards["tiktok"]["quota"]["label"] == "0 of 5 TikTok drafts used today"
    assert cards["instagram"]["mode"] == "kit"


def test_cancelled_slot_can_be_rescheduled(fresh_db):
    cid = _clip(fresh_db)
    yt = _acct("youtube")["id"]
    p = subs.schedule(cid, [yt])[0]
    db.ex("UPDATE posts SET status='cancelled' WHERE id=?", (p["post_id"],))
    again = subs.schedule(cid, [yt])[0]
    assert db.q1("SELECT status FROM posts WHERE id=?", (again["post_id"],))["status"] == "scheduled"


def test_audit_table_and_account_check_never_posts(fresh_db, monkeypatch):
    from clipfarm.posting import audit
    assert {r["platform"] for r in audit.TABLE} >= {"YouTube Shorts", "TikTok", "Instagram Reels", "X"}
    http = FakeHTTP()
    monkeypatch.setattr(instagram, "requests", http.ns())
    res = audit.check_all()
    assert {r["platform"] for r in res} == {"youtube", "tiktok", "instagram"}
    assert all(m != "POST" for m, *_ in http.calls)


def test_submission_checklist_still_works(fresh_db):
    prog = db.q1("SELECT id FROM programs LIMIT 1")["id"]
    cid = _clip(fresh_db, program_id=prog)
    items = platforms.submission_checklist(cid)
    assert items[0]["item"].startswith("Post is live") and items[0]["done"] is False
