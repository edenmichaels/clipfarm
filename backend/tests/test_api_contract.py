"""API contract tests: every FastAPI route, its auth, status codes and response shape.
Needs fastapi + httpx (installed from requirements.txt in CI). No network."""
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from clipfarm import db  # noqa: E402

TOKEN = "test-token-123456"
H = {"X-ClipFarm-Token": TOKEN}
COVERED: set[tuple[str, str]] = set()


def cover(method, path):
    COVERED.add((method.upper(), path))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import requests
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "api.db")
    db.set_setting("media_dir", str(tmp_path / "media"))
    db.set_setting("transcription", {"enabled": False})
    db.set_setting("llm", {"provider": "ollama", "base_url": "http://127.0.0.1:9"})
    from clipfarm import api
    monkeypatch.setattr(api, "TOKEN", TOKEN)
    monkeypatch.setattr(api, "available_providers", lambda: {})
    monkeypatch.setattr(api.disc, "available_providers", lambda: {})
    monkeypatch.setattr("clipfarm.campaigns.matching.available_providers", lambda: {})
    offline = lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError("offline"))  # noqa: E731
    monkeypatch.setattr(requests, "get", offline)
    monkeypatch.setattr(requests, "post", offline)
    return TestClient(api.app)


def call(c, method, path, route=None, expect=200, **kw):
    cover(method, route or path)
    r = c.request(method, path, headers=kw.pop("headers", H), **kw)
    assert r.status_code == expect, (method, path, r.status_code, r.text[:500])
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


def _streamer(status="program_member", login="dylanpage"):
    return db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                    VALUES ('twitch', ?, ?, ?, ?)""", (login + "_id", login, login.title(), status))


def _linked_clip(tmp_path):
    sid = _streamer()
    prog = db.q1("SELECT id FROM programs WHERE streamer_label='Dylan Page'")["id"]
    db.ex("INSERT INTO streamer_programs(streamer_id, program_id, confirmed) VALUES (?,?,1)", (sid, prog))
    f = tmp_path / "media" / "clips" / "a.mp4"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x00" * 100)
    cid = db.ex("""INSERT INTO clips(streamer_id, program_id, file_path, title, description, hashtags, content_hash,
                   review_status, caption_text) VALUES (?,?,?,?,?,?,?,?,?)""",
                (sid, prog, str(f), "T", "D", '["gta6"]', "h1", "approved", "T\n\n#DylanPage"))
    return sid, prog, cid, f


# ---- auth ----------------------------------------------------------------------------------------
def test_auth_and_public_health(client):
    assert call(client, "GET", "/health", headers={})["ok"] is True
    for path in ("/settings", "/events", "/media?path=x", "/campaigns"):
        r = client.get(path)
        assert r.status_code == 401, path
    assert client.get("/settings?token=" + TOKEN).status_code == 200


# ---- system, settings, secrets -------------------------------------------------------------------
def test_system_settings_secrets(client):
    s = call(client, "GET", "/system")
    assert {"encoders", "disk", "connected", "secrets", "vault", "version"} <= set(s)
    st = call(client, "GET", "/settings")
    assert st["usd_to_aud"] == 1.53
    st2 = call(client, "PUT", "/settings", json={"accent": "#3ccf7a"})
    assert st2["accent"] == "#3ccf7a"
    call(client, "POST", "/secrets", json={"name": "nope", "value": "x"}, expect=400)
    call(client, "POST", "/secrets", json={"name": "twitch_client_id", "value": "abcdef123"})
    assert call(client, "GET", "/system")["secrets"]["twitch_client_id"] is True
    call(client, "POST", "/secrets", json={"name": "twitch_client_id", "value": ""})
    assert call(client, "GET", "/system")["secrets"]["twitch_client_id"] is False
    # the secret must never come back out of the API
    assert "abcdef123" not in json.dumps(call(client, "GET", "/settings"))


# ---- games & discovery ---------------------------------------------------------------------------
def test_games_and_discovery(client):
    g = call(client, "GET", "/games/trending")
    assert any(x["name"] == "Grand Theft Auto VI" for x in g)
    assert call(client, "GET", "/games/search?q=gta", route="/games/search")[0]["name"] == "gta"
    gid = call(client, "POST", "/games", json={"name": "Grand Theft Auto VI"})["id"]
    d = call(client, "POST", f"/games/{gid}/discover", route="/games/{game_id}/discover")
    assert d == {"errors": [], "streamers": []}
    assert call(client, "GET", f"/games/{gid}/streamers", route="/games/{game_id}/streamers") == []


# ---- streamers, programs ---------------------------------------------------------------------------
def test_streamers_and_programs(client):
    sid = _streamer("unknown", "someone")
    call(client, "PATCH", f"/streamers/{sid}", route="/streamers/{sid}", json={"permission_status": "bad"}, expect=400)
    r = call(client, "PATCH", f"/streamers/{sid}", route="/streamers/{sid}", json={"best_platforms": ["tiktok"]})
    assert json.loads(r["best_platforms"]) == ["tiktok"]
    pid = call(client, "POST", "/programs", json={"name": "My program", "payout_per_1k": 1.5,
                                                   "required_tags": ["#x"]})["id"]
    progs = call(client, "GET", "/programs")
    mine = next(p for p in progs if p["id"] == pid)
    assert mine["joined"] == 1 and "payout_per_1k" not in json.loads(mine["needs_confirming"])
    call(client, "PUT", f"/programs/{pid}", route="/programs/{pid}", json={"name": "My program", "payout_per_1k": 2.0})
    call(client, "POST", f"/streamers/{sid}/programs/{pid}", route="/streamers/{sid}/programs/{pid}")
    assert db.q1("SELECT permission_status FROM streamers WHERE id=?", (sid,))["permission_status"] == "program_member"
    why = call(client, "GET", f"/streamers/{sid}/why", route="/streamers/{sid}/why")
    assert {"parts", "projections", "campaigns", "no_payout", "notes"} <= set(why)
    call(client, "GET", "/streamers/99999/why", route="/streamers/{sid}/why", expect=404)
    call(client, "DELETE", f"/streamers/{sid}/programs/{pid}", route="/streamers/{sid}/programs/{pid}")
    call(client, "DELETE", f"/programs/{pid}", route="/programs/{pid}")


# ---- projects, jobs -----------------------------------------------------------------------------
def test_projects_need_payout_confirmation(client):
    paid = _streamer()
    prog = db.q1("SELECT id FROM programs WHERE streamer_label='Dylan Page'")["id"]
    db.ex("INSERT INTO streamer_programs(streamer_id, program_id, confirmed) VALUES (?,?,1)", (paid, prog))
    free = _streamer("allowed", "freeguy")
    body = {"name": "p", "game_id": None, "streamer_ids": [paid, free], "duration_sec": 3600, "start": False}
    call(client, "POST", "/projects", json=body, expect=400)                    # disclaimer not accepted yet
    db.set_setting("disclaimer_accepted", True)
    r = client.post("/projects", json=body, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["needs_confirmation"] == ["Freeguy"]
    pid = call(client, "POST", "/projects", json={**body, "confirm_no_payout": True})["id"]
    assert call(client, "POST", "/projects", json={**body, "streamer_ids": [paid]})["id"]   # paid only: no question
    unknown = _streamer("unknown", "unk")
    call(client, "POST", "/projects", json={**body, "streamer_ids": [unknown], "confirm_no_payout": True}, expect=400)
    assert any(p["id"] == pid for p in call(client, "GET", "/projects"))
    for action in ("start", "pause", "resume", "stop"):
        call(client, "POST", f"/projects/{pid}/{action}", route="/projects/{pid}/{action}")
    call(client, "POST", f"/projects/{pid}/explode", route="/projects/{pid}/{action}", expect=404)
    jobs = call(client, "GET", "/jobs?active=false", route="/jobs")
    assert jobs
    call(client, "POST", f"/jobs/{jobs[0]['id']}/retry", route="/jobs/{jid}/retry")


# ---- clips, media, posting, submissions, payouts --------------------------------------------------
def test_clips_posting_submissions(client, tmp_path):
    sid, prog, cid, f = _linked_clip(tmp_path)
    clips = call(client, "GET", "/clips")
    assert clips[0]["id"] == cid and "views" in clips[0] and "earnings" in clips[0]
    r = call(client, "PATCH", f"/clips/{cid}", route="/clips/{cid}", json={"title": "New title"})
    assert r["title"] == "New title"
    exp = call(client, "POST", f"/clips/{cid}/export", route="/clips/{cid}/export", json={"dest": str(tmp_path / "out")})
    assert "#DylanPage" in Path(exp["caption_file"]).read_text(encoding="utf-8")
    assert call(client, "GET", f"/clips/{cid}/checklist", route="/clips/{cid}/checklist")
    assert client.get(f"/media?path={f}&token={TOKEN}").status_code == 200
    cover("GET", "/media")
    assert client.get(f"/media?path={tmp_path / 'api.db'}&token={TOKEN}").status_code == 404   # outside media folder
    accts = call(client, "GET", "/accounts")
    tiktok = next(a for a in accts if a["platform"] == "tiktok")
    assert tiktok["handle"] == "@vira1gta6clips" and tiktok["oauth_status"] == "not_connected"
    call(client, "PATCH", f"/accounts/{tiktok['id']}", route="/accounts/{aid}", json={"followers": 120})
    sched = call(client, "POST", f"/clips/{cid}/schedule", route="/clips/{cid}/schedule",
                 json={"account_ids": [tiktok["id"]]})
    assert sched[0]["manual"] is True
    call(client, "POST", f"/clips/{cid}/schedule", route="/clips/{cid}/schedule",
         json={"account_ids": [tiktok["id"]]}, expect=409)                     # never twice to one account
    out = call(client, "POST", f"/clips/{cid}/posted", route="/clips/{cid}/posted",
               json={"account_id": tiktok["id"], "url": "https://www.tiktok.com/@vira1gta6clips/video/9"})
    item = out["submission"]
    assert item["label"].startswith("Submit to Dylan Page") and item["label"].endswith("on Content Rewards")
    call(client, "POST", f"/clips/{cid}/posted", route="/clips/{cid}/posted",
         json={"account_id": tiktok["id"], "url": "https://x"}, expect=409)
    assert call(client, "GET", "/posts?status=posted", route="/posts")[0]["url"].endswith("/9")
    subs = call(client, "GET", "/submissions")
    assert subs[0]["id"] == item["id"]
    call(client, "POST", f"/submissions/{item['id']}", route="/submissions/{sub_id}", json={"done": True})
    call(client, "POST", f"/submissions/{item['id']}", route="/submissions/{sub_id}", json={"done": True}, expect=409)
    call(client, "POST", "/submissions/9999", route="/submissions/{sub_id}", json={"done": True}, expect=404)
    post_id = out["post_id"]
    call(client, "POST", f"/posts/{post_id}/metrics", route="/posts/{post_id}/metrics", json={"views": 12000})
    call(client, "POST", f"/posts/{post_id}/payout", route="/posts/{post_id}/payout",
         json={"status": "approved", "amount": 12.0})
    call(client, "POST", f"/posts/{post_id}/payout", route="/posts/{post_id}/payout",
         json={"status": "weird"}, expect=400)
    e = call(client, "GET", "/earnings")
    assert e["est_vs_actual"][0]["actual_aud"] == pytest.approx(12.0 * 1.53)
    c2 = db.ex("INSERT INTO clips(streamer_id, program_id, file_path, content_hash) VALUES (?,?,?, 'h2')",
               (sid, prog, str(f)))
    p2 = call(client, "POST", f"/clips/{c2}/schedule", route="/clips/{cid}/schedule",
              json={"account_ids": [tiktok["id"]]})[0]["post_id"]
    call(client, "POST", f"/posts/{p2}/cancel", route="/posts/{post_id}/cancel")


# ---- campaigns, projections, auto-pilot ---------------------------------------------------------------
PAGE = ("$2,500 AsianJeff Clipping Campaign\nGet paid to clip AsianJeff's GTA 6 https://www.twitch.tv/asianjeff\n"
        "TikTok\nPer 1K views\n$1.00\nMin payout\n$10.00\nMax payout\n$300.00\nBudget\n$100 / $2,500\n")


def test_campaigns_projections_autopilot(client):
    camps = call(client, "GET", "/campaigns")
    assert len([c for c in camps if c["operator"] == "content_rewards"]) == 12
    assert all(isinstance(c["needs_confirming"], list) for c in camps)
    call(client, "POST", "/campaigns/import", json={"text": "hello"}, expect=400)
    imp = call(client, "POST", "/campaigns/import", json={"text": PAGE})
    assert imp["campaign"]["budget_remaining"] == 2400 and "payout_per_1k" in imp["found"]
    cid = imp["id"]
    patched = call(client, "PATCH", f"/campaigns/{cid}", route="/campaigns/{pid}", json={"pays_au": 1})
    assert "pays_au" not in patched["needs_confirming"]
    call(client, "PATCH", "/campaigns/99999", route="/campaigns/{pid}", json={"x": 1}, expect=404)
    m = call(client, "POST", "/campaigns/match")
    assert "results" in m and "pending" in m
    assert isinstance(call(client, "GET", "/campaigns/matches"), list)
    call(client, "POST", "/campaigns/matches/9999", route="/campaigns/matches/{mid}", json={"accept": True}, expect=404)
    sid = _streamer(login="asianjeff")
    db.ex("INSERT INTO streamer_programs(streamer_id, program_id, confirmed) VALUES (?,?,1)", (sid, cid))
    rows = call(client, "POST", "/projections/refresh")
    assert rows and {"per_clip_low", "per_clip_likely", "per_clip_high", "d7_likely", "d30_likely",
                     "aud_per_pc_hour", "warnings"} <= set(rows[0])
    assert call(client, "GET", "/projections")
    assert call(client, "POST", "/forecast/refit")["fitted"] is False
    ap = call(client, "GET", "/autopilot")
    assert ap["settings"]["enabled"] is False
    ap2 = call(client, "PUT", "/autopilot", json={"enabled": True, "max_clips_per_day": 8})
    assert ap2["settings"]["max_clips_per_day"] == 8
    assert db.q1("SELECT 1 FROM jobs WHERE kind='autopilot_tick'")
    run = call(client, "POST", "/autopilot/run")
    assert {"plan", "log", "status_changes"} <= set(run)
    call(client, "PUT", "/autopilot", json={"enabled": False})


# ---- health, backup, dashboard, logs, events, oauth ----------------------------------------------------
def test_health_backup_misc(client, tmp_path):
    run = call(client, "POST", "/health/run", json={"only": ["engine", "twitch", "llm"]})
    assert {c["id"] for c in run["checks"]} >= {"engine", "twitch", "llm"}
    chk = call(client, "GET", "/health/checks")
    assert "summary" in chk and "banner" in chk["summary"]
    fx = call(client, "POST", "/health/fix/recheck", route="/health/fix/{fix_id}")
    assert fx["ok"] is True
    assert call(client, "POST", "/health/fix/open_connections", route="/health/fix/{fix_id}")["ok"] is False
    diag = call(client, "GET", "/health/diagnostics")
    assert "ClipFarm diagnostics" in diag["text"]
    call(client, "POST", "/secrets", json={"name": "youtube_api_key", "value": "AIzaBackupTest0000000000000000000000"})
    bfile = tmp_path / "backup.clipfarm"
    call(client, "POST", "/backup/export", json={"password": "short"}, expect=400)
    call(client, "POST", "/backup/export", json={"password": "a long password", "path": str(bfile)})
    assert b"AIzaBackupTest" not in bfile.read_bytes()
    call(client, "POST", "/backup/import", json={"password": "wrong password!", "path": str(bfile)}, expect=400)
    res = call(client, "POST", "/backup/import", json={"password": "a long password", "path": str(bfile)})
    assert res["secrets"] >= 1
    d = call(client, "GET", "/dashboard")
    assert {"clips", "projects_active", "jobs_running", "earnings", "disk"} <= set(d)
    assert isinstance(call(client, "GET", "/logs"), list)
    cover("GET", "/events")                       # auth checked in test_auth_and_public_health (stream never ends)
    assert "Google OAuth" in call(client, "GET", "/oauth/youtube/start", expect=400)["detail"]
    r = client.get("/oauth/youtube/callback?code=x&state=bogus")
    cover("GET", "/oauth/youtube/callback")
    assert r.status_code == 400 and "expired" in r.text


def test_every_route_is_covered():
    from clipfarm import api
    routes = set()
    for r in api.app.routes:
        for m in getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}:
            if r.path.startswith(("/docs", "/openapi", "/redoc")):
                continue
            routes.add((m, r.path))
    missing = routes - COVERED
    assert not missing, f"Routes without a contract test: {sorted(missing)}"
