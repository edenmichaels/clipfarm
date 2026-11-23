"""API contract tests: every FastAPI route, its auth, status codes and response shape.
Needs fastapi + httpx (installed from requirements.txt in CI). No network."""
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from clipfarm import db  # noqa: E402
from clipfarm.jobs import engine as eng  # noqa: E402

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
    assert {"clips", "projects_active", "jobs_running", "earnings", "disk", "earnings_week_aud", "to_submit",
            "projects_progress"} <= set(d)
    assert isinstance(call(client, "GET", "/logs"), list)
    cover("GET", "/events")                       # auth checked in test_auth_and_public_health (stream never ends)
    assert "Google OAuth" in call(client, "GET", "/oauth/youtube/start", expect=400)["detail"]
    r = client.get("/oauth/youtube/callback?code=x&state=bogus")
    cover("GET", "/oauth/youtube/callback")
    assert r.status_code == 400 and "expired" in r.text


# ---- v0.3: page import, match on click, grouped list, project auto-fill ------------------------------
def test_v03_routes(client):
    from test_v02 import PAGE
    url = "https://contentrewards.com/c/campaigns/bbd3a963-9a23-457c-bc15-6697971d9902"
    r = call(client, "POST", "/campaigns/import-page", json={"url": url, "text": PAGE, "title": "x", "links": []})
    assert r["total"] == 1 and r["summary"].startswith("Imported 1 campaign")
    call(client, "POST", "/campaigns/import-page", json={"url": url, "text": "x" * 400_001}, expect=413)
    r = call(client, "POST", "/campaigns/import-paste", json={"text": PAGE.replace("$1.00", "$1.25"), "url": url})
    assert r["total"] == 1
    call(client, "POST", "/campaigns/import-paste", json={"text": "nothing here"}, expect=400)
    kreek = db.q1("SELECT id FROM programs WHERE streamer_label='KreekCraft'")["id"]
    c = call(client, "POST", f"/campaigns/{kreek}/joined", route="/campaigns/{pid}/joined", json={"joined": False})
    assert c["joined"] == 0
    call(client, "POST", f"/campaigns/{kreek}/joined", route="/campaigns/{pid}/joined", json={"joined": True})
    call(client, "POST", "/campaigns/99999/joined", route="/campaigns/{pid}/joined", json={}, expect=404)

    sid = _streamer("unknown", "kreekcraft")
    q = call(client, "POST", f"/streamers/{sid}/match", route="/streamers/{sid}/match")
    assert q["state"] == "ask"
    a = call(client, "POST", f"/streamers/{sid}/match/answer", route="/streamers/{sid}/match/answer",
             json={"program_id": kreek})
    assert a["state"] == "matched"
    assert call(client, "POST", f"/streamers/{sid}/match", route="/streamers/{sid}/match")["state"] == "matched"
    call(client, "POST", "/streamers/99999/match", route="/streamers/{sid}/match", expect=404)
    call(client, "POST", "/streamers/99999/match/answer", route="/streamers/{sid}/match/answer", json={}, expect=404)

    gid = call(client, "POST", "/games", json={"name": "Grand Theft Auto VI"})["id"]
    g = call(client, "GET", f"/games/{gid}/streamers/grouped", route="/games/{game_id}/streamers/grouped")
    assert {"joined", "worth_joining", "worth_joining_left_out", "no_campaign", "min_aud_per_clip"} <= set(g)

    d = call(client, "GET", "/projects/defaults")
    assert d["platforms"] and d["duration_sec"]
    f = call(client, "POST", "/projects/autofill", json={"streamer_ids": [sid], "platforms": ["tiktok", "x"]})
    assert f["rewards_account"]["username"] == "grandtheftclipper" and "Whop: grandtheftclipper" in f["summary"]
    assert f["streamers"][0]["campaign"]["id"] == kreek


# ---- v0.4: captions, Review editing, feedback, weights, music, trending --------------------------------
def test_v04_routes(client, tmp_path):
    pres = call(client, "GET", "/captions/presets")
    assert {p["key"] for p in pres["presets"]} >= {"bold_pop", "hype_yellow", "clean_white", "minimal"}
    r = client.get("/fonts/Anton-Regular.ttf")
    cover("GET", "/fonts/{name}")
    assert r.status_code == 200 and len(r.content) > 10000
    assert client.get("/fonts/..%2Fsecrets.py").status_code == 404
    _, _, cid, _ = _linked_clip(tmp_path)
    db.ex("UPDATE clips SET words=? WHERE id=?", (json.dumps([{"word": "hey", "start": 1, "end": 1.4}]), cid))
    info = call(client, "GET", f"/clips/{cid}/edit", route="/clips/{cid}/edit")
    assert info["editable"] is False and "feedback_tags" in info and isinstance(info["trending"], list)
    call(client, "GET", "/clips/99999/edit", route="/clips/{cid}/edit", expect=404)
    call(client, "POST", f"/clips/{cid}/rerender", route="/clips/{cid}/rerender", json={"caption_nudge": -40}, expect=409)
    pv = call(client, "POST", f"/clips/{cid}/caption-preview", route="/clips/{cid}/caption-preview",
              json={"caption_nudge": -40, "caption_style": "minimal"})
    assert pv["events"] and pv["preset"] == "minimal"
    fb = call(client, "POST", f"/clips/{cid}/feedback", route="/clips/{cid}/feedback",
              json={"rating": 1, "tags": ["great timing"]})
    assert fb["ratings"] == 1 and fb["needed"] == 50
    call(client, "POST", f"/clips/{cid}/feedback", route="/clips/{cid}/feedback", json={"rating": 5}, expect=400)
    assert call(client, "POST", f"/clips/{cid}/export-for-trending", route="/clips/{cid}/export-for-trending")["job_id"]
    w = call(client, "GET", "/highlights/weights")
    assert w["default"]["audio_events"] == 0.30 and w["focus"] == "balanced"
    w = call(client, "PUT", "/highlights/weights", json={"weights": {"chat": 2, "audio_events": 2}, "focus": "funny"})
    assert w["focus"] == "funny" and abs(sum(w["global"].values()) - 1) < 0.02
    call(client, "PUT", "/highlights/weights", json={"focus": "nope"}, expect=400)
    w = call(client, "POST", "/highlights/weights/reset", json={})
    assert w["global"]["audio_events"] == 0.30
    m = call(client, "GET", "/music/tracks")
    assert len(m["tracks"]) >= 8 and all(t["licence"] for t in m["tracks"])
    tid = m["tracks"][0]["id"]
    t = call(client, "PATCH", f"/music/tracks/{tid}", route="/music/tracks/{tid}", json={"moods": ["chill"]})
    assert t["moods"] == ["chill"] and t["tags_edited"] == 1
    call(client, "PATCH", "/music/tracks/99999", route="/music/tracks/{tid}", json={}, expect=404)
    folder = tmp_path / "mymusic"
    folder.mkdir()
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=12", str(folder / "a.mp3")],
                   check=True)
    sc = call(client, "POST", "/music/scan", json={"folder": str(folder)})
    assert sc["added"] == 1 and "responsible" in sc["warning"]
    call(client, "POST", "/music/scan", json={"folder": str(tmp_path / "missing")}, expect=400)
    assert call(client, "PUT", "/music/settings", json={"duck_db": 30})["duck_db"] == 18
    imp = call(client, "POST", "/trending/import", json={"text": "Funny Sound - Someone | funny"})
    assert imp["added"] == 1
    call(client, "POST", "/trending/import", json={"text": " "}, expect=400)
    lst = call(client, "GET", "/trending")
    call(client, "DELETE", f"/trending/{lst[0]['id']}", route="/trending/{tid}")


# ---- v0.5: edit projects ------------------------------------------------------------------------------
def test_v05_edit_routes(client, tmp_path):
    import subprocess
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=12", "-f", "lavfi",
                    "-i", "sine=f=300:d=12", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(src)], check=True)
    _, _, cid, _ = _linked_clip(tmp_path)
    db.ex("UPDATE clips SET source_path=?, words=? WHERE id=?", (str(src), json.dumps(
        [{"word": w, "start": 1 + i * 0.6, "end": 1.4 + i * 0.6} for i, w in enumerate("so um we did it".split())]), cid))
    v = call(client, "GET", f"/edit/{cid}", route="/edit/{cid}")
    assert v["edl"]["source"]["path"] == str(src) and v["duration"] > 10 and v["fillers"] == [1]
    call(client, "GET", "/edit/99999", route="/edit/{cid}", expect=404)
    e = v["edl"]
    e["captions"]["nudge"] = -30
    v = call(client, "PUT", f"/edit/{cid}", route="/edit/{cid}", json={"edl": e, "label": "Nudge"})
    assert v["head"] == 2
    call(client, "PUT", f"/edit/{cid}", route="/edit/{cid}", json={"edl": {"range": [0, 1]}}, expect=400)
    v = call(client, "POST", f"/edit/{cid}/transcript", route="/edit/{cid}/transcript", json={"op": "fillers"})
    assert v["edl"]["cuts"] and v["edl"]["captions"]["words"][1]["deleted"]
    call(client, "POST", f"/edit/{cid}/transcript", route="/edit/{cid}/transcript", json={"op": "range", "start": 1, "end": 1.2},
         expect=400)
    call(client, "POST", f"/edit/{cid}/transcript", route="/edit/{cid}/transcript", json={"op": "nope"}, expect=400)
    v = call(client, "POST", f"/edit/{cid}/transcript", route="/edit/{cid}/transcript", json={"op": "fix", "index": 4, "text": "IT!"})
    assert v["edl"]["captions"]["words"][4]["word"] == "IT!"
    assert call(client, "POST", f"/edit/{cid}/undo", route="/edit/{cid}/undo")["edl"]["captions"]["words"][4]["word"] == "it"
    assert call(client, "POST", f"/edit/{cid}/redo", route="/edit/{cid}/redo")["edl"]["captions"]["words"][4]["word"] == "IT!"
    assert len(call(client, "GET", f"/edit/{cid}/history", route="/edit/{cid}/history")) >= 3
    v = call(client, "POST", f"/edit/{cid}/reframe", route="/edit/{cid}/reframe")
    assert v["edl"]["layout"]["keyframes"]
    v = call(client, "POST", f"/edit/{cid}/facecam", route="/edit/{cid}/facecam",
             json={"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.3})
    assert v["edl"]["layout"]["facecam"]["x"] == 0.7
    call(client, "POST", f"/edit/{cid}/facecam", route="/edit/{cid}/facecam", json={"x": 0.9, "y": 0, "w": 0.5, "h": 0.3},
         expect=400)
    call(client, "POST", f"/edit/{cid}/fix-rules", route="/edit/{cid}/fix-rules")
    call(client, "POST", f"/edit/{cid}/translate", route="/edit/{cid}/translate", json={"language": "es"}, expect=400)
    call(client, "POST", f"/edit/{cid}/translate", route="/edit/{cid}/translate", json={})
    assert call(client, "POST", f"/edit/{cid}/preview", route="/edit/{cid}/preview")["job_id"]
    assert call(client, "POST", f"/edit/{cid}/export", route="/edit/{cid}/export", json={"preset": "shorts"})["job_id"]
    call(client, "POST", f"/edit/{cid}/export", route="/edit/{cid}/export", json={"preset": "vhs"}, expect=400)
    assert "tiktok" in call(client, "GET", "/export-presets")
    t = call(client, "GET", "/caption-templates")
    assert len(t["templates"]) >= 4 and "karaoke" in t["animations"]
    mine = call(client, "POST", "/caption-templates", json={"name": "Mine", "template": {"base": "minimal", "text": "#00ff00"}})
    assert mine["template"]["text"] == "00FF00"
    call(client, "POST", "/caption-templates", json={"template": {}}, expect=400)
    d = call(client, "PUT", "/caption-templates/defaults", json={"scope": "account:1", "key": mine["key"]})
    assert d["account:1"] == mine["key"]
    call(client, "PUT", "/caption-templates/defaults", json={"scope": "everywhere", "key": "x"}, expect=400)
    call(client, "DELETE", f"/caption-templates/{mine['id']}", route="/caption-templates/{tid}")


def test_v06_editor_routes(client, tmp_path):
    import subprocess
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=30", "-f", "lavfi",
                    "-i", "sine=f=300:d=30", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(src)], check=True)
    _, prog, cid, _ = _linked_clip(tmp_path)
    db.ex("UPDATE clips SET source_path=?, words=? WHERE id=?", (str(src), json.dumps(
        [{"word": w, "start": 4 + i * 0.6, "end": 4.4 + i * 0.6} for i, w in enumerate("um so we um did it no way".split())]),
        cid))
    v = call(client, "GET", f"/edit/{cid}", route="/edit/{cid}")
    assert "#DylanPage" in v["edl"]["rules"]["tags"]
    fr = client.get(f"/edit/{cid}/frame?t=1.0", headers=H)
    assert fr.status_code == 200 and fr.headers["content-type"] == "image/png" and fr.content[:4] == b"\x89PNG"
    COVERED.add(("GET", "/edit/{cid}/frame"))
    cv = call(client, "GET", f"/edit/{cid}/canvas", route="/edit/{cid}/canvas")
    assert cv["events"] and cv["style"]["family"] and cv["duration"] > 20
    s = call(client, "POST", f"/edit/{cid}/score", route="/edit/{cid}/score")
    assert 0 <= s["virality"]["score"] <= 100 and s["virality"]["reason"] and isinstance(s["flags"], list)
    rows = call(client, "GET", "/editor/clips")
    assert rows and rows[0]["id"] == cid and rows[0]["virality"] is not None and rows[0]["status"] in ("new", "edited", "ready")
    assert call(client, "GET", "/editor/clips?sort=virality&min_score=101") == []
    call(client, "GET", "/editor/clips?sort=random", expect=400)
    e = v["edl"]
    e["texts"] = {"tiktok": {"title": "t", "description": "d", "hashtags": ["gta6"]}}
    call(client, "PUT", f"/edit/{cid}", route="/edit/{cid}", json={"edl": e})
    flags = call(client, "POST", f"/edit/{cid}/score", route="/edit/{cid}/score")["flags"]
    assert any(f["id"] == "hashtags:tiktok" for f in flags)
    v = call(client, "POST", f"/edit/{cid}/fix", route="/edit/{cid}/fix", json={"id": "hashtags:tiktok"})
    assert "#DylanPage" in v["edl"]["texts"]["tiktok"]["hashtags"]
    call(client, "POST", f"/edit/{cid}/fix", route="/edit/{cid}/fix", json={"all": True})
    call(client, "POST", f"/edit/{cid}/fix", route="/edit/{cid}/fix", json={}, expect=400)
    f = call(client, "POST", f"/edit/{cid}/fix-my-clip", route="/edit/{cid}/fix-my-clip")
    assert "removed filler words" in f["changes"]
    a = call(client, "POST", f"/edit/{cid}/auto-edit", route="/edit/{cid}/auto-edit")
    assert a["edl"]["captions"]["words"] and "changes" in a
    call(client, "POST", f"/edit/{cid}/signal", route="/edit/{cid}/signal", json={"kind": "keep"})
    call(client, "POST", f"/edit/{cid}/signal", route="/edit/{cid}/signal", json={"kind": "nope"}, expect=400)
    b = call(client, "POST", "/editor/batch", json={"ids": [cid], "action": "template", "template": "hype_yellow"})
    assert b["done"] == [cid]
    assert call(client, "GET", f"/edit/{cid}", route="/edit/{cid}")["edl"]["captions"]["template"] == "hype_yellow"
    assert call(client, "POST", "/editor/batch", json={"ids": [cid], "action": "export", "preset": "shorts"})["jobs"]
    call(client, "POST", "/editor/batch", json={"ids": [cid], "action": "melt"}, expect=400)
    t = call(client, "POST", "/trends", json={"text": "#heist\nnew map\n"})
    assert t["added"] == 2 and {x["topic"] for x in call(client, "GET", "/trends")} == {"heist", "new map"}
    call(client, "DELETE", f"/trends/{t['topics'][0]['id']}", route="/trends/{tid}")
    call(client, "POST", "/editor/batch", json={"ids": [cid], "action": "delete"})
    assert call(client, "GET", "/editor/clips") == []


def test_v07_routes(client, tmp_path):
    import subprocess
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=30", "-f", "lavfi",
                    "-i", "sine=f=300:d=30", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(src)], check=True)
    _, prog, cid, _ = _linked_clip(tmp_path)
    db.ex("UPDATE clips SET source_path=?, words=? WHERE id=?", (str(src), json.dumps(
        [{"word": w, "start": 4 + i * 0.6, "end": 4.4 + i * 0.6} for i, w in enumerate("no way the cops are here bro".split())]),
        cid))
    v = call(client, "POST", f"/edit/{cid}/hooks", route="/edit/{cid}/hooks")
    assert len(v["edl"]["hook"]["options"]) >= 3 and "dylanpage" in v["edl"]["texts"]["tiktok"]["hashtags"]
    v = call(client, "POST", f"/edit/{cid}/effects", route="/edit/{cid}/effects", json={})
    assert any(z.get("auto") for z in v["edl"]["zooms"])
    v = call(client, "POST", f"/edit/{cid}/effects", route="/edit/{cid}/effects", json={"clear": True})
    assert not any(z.get("auto") for z in v["edl"]["zooms"])
    fx = call(client, "GET", "/sfx")
    assert any(x["id"] == "boom" for x in fx)
    v = call(client, "POST", f"/edit/{cid}/sfx", route="/edit/{cid}/sfx", json={"id": "boom", "t_out": 1.0})
    assert v["edl"]["sfx"][-1]["id"] == "boom"
    call(client, "POST", f"/edit/{cid}/sfx", route="/edit/{cid}/sfx", json={"id": "nope"}, expect=404)
    lib = tmp_path / "broll"
    lib.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-frames:v", "1",
                    str(lib / "cops.png")], check=True)
    call(client, "PUT", "/library-folders", json={"broll_dir": str(lib)}, expect=400)
    call(client, "PUT", "/library-folders", json={"broll_dir": str(tmp_path / "missing"), "confirm_rights": True}, expect=400)
    assert call(client, "PUT", "/library-folders", json={"broll_dir": str(lib), "confirm_rights": True})["broll_dir"]
    assert call(client, "GET", "/broll")["items"][0]["name"] == "cops"
    v = call(client, "POST", f"/edit/{cid}/broll", route="/edit/{cid}/broll", json={"path": str(lib / "cops.png"), "t_out": 2})
    assert v["edl"]["broll"]
    call(client, "POST", f"/edit/{cid}/broll", route="/edit/{cid}/broll", json={"path": str(src)}, expect=400)
    r = call(client, "POST", f"/edit/{cid}/music", route="/edit/{cid}/music", json={})
    assert r["edl"]["music"]["enabled"] and r["edl"]["music"]["licence"]
    call(client, "POST", f"/edit/{cid}/music", route="/edit/{cid}/music", json={"track_id": 99999}, expect=404)
    var = call(client, "POST", f"/edit/{cid}/variants", route="/edit/{cid}/variants", json={"n": 2})["variants"]
    assert [x["label"] for x in var] == ["B", "C"]
    call(client, "POST", f"/edit/{cid}/variants", route="/edit/{cid}/variants", json={"n": 9}, expect=400)
    aid = db.ex("INSERT INTO accounts(platform, handle) VALUES ('tiktok','@brand')")
    b = call(client, "POST", "/brand-templates", json={"name": "Mine", "data": {"progress_color": "00ff00"}, "account_ids": [aid]})
    assert b["progress_color"] == "00FF00" and b["accounts"] == [aid]
    call(client, "POST", "/brand-templates", json={"name": "", "data": {}}, expect=400)
    assert call(client, "GET", "/brand-templates")["templates"][0]["name"] == "Mine"
    call(client, "PUT", f"/accounts/{aid}/brand", route="/accounts/{aid}/brand", json={"brand_template_id": None})
    call(client, "PUT", f"/accounts/{aid}/brand", route="/accounts/{aid}/brand", json={"brand_template_id": 999}, expect=404)
    v = call(client, "POST", f"/edit/{cid}/brand", route="/edit/{cid}/brand", json={"brand_template_id": b["id"]})
    assert v["edl"]["brand"]["name"] == "Mine"
    call(client, "DELETE", f"/brand-templates/{b['id']}", route="/brand-templates/{bid}")
    ls = call(client, "GET", "/learning")
    assert "virality_default" in ls and "predicted_vs_actual" in ls
    w = call(client, "PUT", "/virality/weights", json={"weights": {"hook": 1}})
    assert abs(sum(w["virality_global"].values()) - 1) < 0.02
    w = call(client, "POST", "/virality/weights/reset", json={})
    assert w["virality_global"]["hook"] == 0.20


def test_v071_engine_and_source_routes(client, tmp_path, monkeypatch):
    import os
    import time as _t
    st = call(client, "GET", "/engine/status")
    assert st["paused"] is False and "children" in st
    assert call(client, "POST", "/engine/pause")["paused"] is True
    assert call(client, "GET", "/engine/status")["paused"] is True
    assert call(client, "POST", "/engine/resume")["paused"] is False
    sid = _streamer()
    src = db.ex("INSERT INTO sources(streamer_id, kind, platform_id, url, status, error) VALUES (?,?,?,?,?,?)",
                (sid, "featured_clip", "x1", "https://clips.twitch.tv/x1", "error", "boom"))
    probs = call(client, "GET", "/sources/problems")
    assert probs[0]["id"] == src and probs[0]["error"] == "boom"
    call(client, "POST", f"/sources/{src}/retry", route="/sources/{sid}/retry", expect=409)     # never processed
    eng.enqueue("process_source", {"project_id": None, "source_id": src})
    db.ex("UPDATE jobs SET status='failed' WHERE kind='process_source'")
    r = call(client, "POST", f"/sources/{src}/retry", route="/sources/{sid}/retry")
    assert r["job_id"] and db.q1("SELECT status, error FROM sources WHERE id=?", (src,))["status"] == "new"
    call(client, "POST", "/sources/99999/retry", route="/sources/{sid}/retry", expect=404)
    exits = []
    monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))
    try:
        assert call(client, "POST", "/engine/shutdown")["ok"]
        _t.sleep(0.6)
        assert exits == [0]
    finally:
        eng.PAUSED.clear()


def test_v08_viral_routes_and_tag_filter(client, tmp_path):
    from clipfarm.highlights import features, viral_data
    st = call(client, "GET", "/viral/status")
    assert st["models"] == [] and st["settings"]["viral_threshold"] == 35
    # settings groups are merged, not replaced
    call(client, "PUT", "/settings", json={"highlights": {"focus": "funny", "visual": False}})
    call(client, "PUT", "/settings", json={"highlights": {"viral_threshold": 50}})
    h = call(client, "GET", "/settings")["highlights"]
    assert h["focus"] == "funny" and h["visual"] is False and h["viral_threshold"] == 50
    gid = db.ex("INSERT INTO games(name, twitch_id) VALUES ('Grand Theft Auto VI','1')")
    import random
    r = random.Random(1)
    for i in range(60):
        f = {k: r.random() * 0.3 for k in features.FEATURES}
        viral = i % 2 == 0
        if viral:
            f["laugh"] = 0.9
        viral_data.add_example(game_id=gid, streamer_id=None, kind="twitch_clip" if viral else "negative",
                               platform_id=f"x{i}", label=1.0 if viral else 0.0, features=f, day=f"2026-11-{10 + i % 3}",
                               vod_offset=float(i))
    res = call(client, "POST", "/viral/retrain")
    assert any(m["game_id"] == gid for m in res["models"]) and res["dataset"][0]["total"] == 60
    assert call(client, "POST", "/viral/reset", json={"game_id": gid})["models"] == [m for m in res["models"] if m["game_id"] != gid]
    assert call(client, "POST", "/viral/refresh")["jobs"]
    assert call(client, "GET", "/viral/report")["report"] is None
    assert call(client, "POST", "/viral/report", json={})["job_id"]
    # filter clips by what happened + how they reacted
    _, prog, cid, _ = _linked_clip(tmp_path)
    sid = db.q1("SELECT streamer_id FROM clips WHERE id=?", (cid,))["streamer_id"]
    src = db.ex("INSERT INTO sources(streamer_id, kind, platform_id, url) VALUES (?, 'vod', 'v88', 'u')", (sid,))
    mid = db.ex("""INSERT INTO moments(source_id, start_sec, end_sec, score, signals, tags, features, viral_score)
                   VALUES (?, 0, 20, 0.8, '{}', '["explosion","police"]', '{"laugh": 0.9}', 88)""", (src,))
    db.ex("UPDATE clips SET moment_id=?, source_path=? WHERE id=?", (mid, str(tmp_path / "s.mp4"), cid))
    assert [c["id"] for c in call(client, "GET", "/editor/clips?tag=explosion&reaction=laugh")] == [cid]
    assert call(client, "GET", "/editor/clips?tag=explosion")[0]["moment_tags"] == ["explosion", "police"]
    assert call(client, "GET", "/editor/clips?tag=boat") == []
    assert call(client, "GET", "/editor/clips?tag=explosion&reaction=scream") == []


def test_v08_styles_and_style_refs(client, tmp_path):
    import base64
    import io
    import subprocess
    from PIL import Image
    st = call(client, "GET", "/styles")
    assert [x["key"] for x in st["styles"]][:5] == ["streamer_split", "full_reaction", "gameplay_only",
                                                     "meme_high_energy", "clean"]
    assert st["fields"]["layout.cam_ratio"] == [0.35, 0.45]
    mine = dict(st["styles"][0], name="Mine")
    mine["layout"]["cam_ratio"] = 0.43
    saved = call(client, "POST", "/styles", json={"name": "Mine", "style": mine})
    assert saved["key"].startswith("user:") and saved["layout"]["cam_ratio"] == 0.43
    assert call(client, "PUT", "/styles/defaults", json={"scope": "streamer:1", "key": saved["key"]})["defaults"] == \
        {"streamer:1": saved["key"]}
    call(client, "PUT", "/styles/defaults", json={"scope": "streamer:1", "key": "nope"}, expect=400)
    buf = io.BytesIO()
    Image.new("RGB", (540, 960), (90, 90, 90)).save(buf, "PNG")
    data = base64.b64encode(buf.getvalue()).decode()
    ref = call(client, "POST", "/style-refs", json={"data": data, "notes": "split w/ seam caps", "tags": ["fail"]})
    call(client, "POST", "/style-refs", json={"data": "bm90IGFuIGltYWdl"}, expect=400)
    rid = ref["id"]
    assert call(client, "GET", "/style-refs")["refs"][0]["notes"] == "split w/ seam caps"
    assert call(client, "PUT", f"/style-refs/{rid}", route="/style-refs/{rid}", json={"tags": ["rage"]})["tags"] == ["rage"]
    r = client.get(f"/style-refs/{rid}/image", headers=H)
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    COVERED.add(("GET", "/style-refs/{rid}/image"))
    made = call(client, "POST", f"/style-refs/{rid}/make-style", route="/style-refs/{rid}/make-style",
                json={"save": True, "name": "From ref"})
    assert made["saved"]["name"] == "From ref" and made["method"] == "image analysis"
    assert call(client, "POST", "/styles/from-image", json={"data": data})["style"]["layout"]["mode"]
    # apply a style to a clip's edit project
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=12", "-f", "lavfi",
                    "-i", "sine=f=300:d=12", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(src)], check=True)
    _, prog, cid, _ = _linked_clip(tmp_path)
    db.ex("UPDATE clips SET source_path=? WHERE id=?", (str(src), cid))
    call(client, "GET", f"/edit/{cid}", route="/edit/{cid}")
    v = call(client, "POST", f"/edit/{cid}/style", route="/edit/{cid}/style", json={"key": "meme_high_energy"})
    assert v["edl"]["style"] == {"key": "meme_high_energy", "name": "Meme / High Energy", "source": "clip"}
    v = call(client, "POST", f"/edit/{cid}/style", route="/edit/{cid}/style", json={"style": {**mine, "name": "Tweak"}})
    assert v["edl"]["layout"]["cam_h"] == 826
    call(client, "POST", f"/edit/{cid}/style", route="/edit/{cid}/style", json={"key": "bogus"}, expect=400)
    call(client, "DELETE", f"/style-refs/{rid}", route="/style-refs/{rid}")
    call(client, "DELETE", f"/styles/{saved['id']}", route="/styles/{sid}")
    assert all(x["key"] != saved["key"] for x in call(client, "GET", "/styles")["styles"])


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
