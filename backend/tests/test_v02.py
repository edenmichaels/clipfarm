"""v0.2 tests: migrations, keys/vault, redaction, campaigns, projections, auto-pilot, render rules,
posting safety and the submission checklist. No network, no real API keys."""
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from clipfarm import compliance, db, migrations, secrets, vault
from clipfarm.campaigns import autopilot, forecast, importer, matching, rules
from clipfarm.campaigns.fields import needs_confirming
from clipfarm.providers import StreamerHit

HAS_FFMPEG = shutil.which("ffmpeg") is not None
SAMPLE = Path(__file__).parent / "data" / "sample_stream.mp4"


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    db.init_db(tmp_path / "t.db")
    db.set_setting("media_dir", str(tmp_path / "media"))
    db.set_setting("transcription", {"enabled": False})
    db.set_setting("llm", {"provider": "ollama", "base_url": "http://127.0.0.1:9"})
    return tmp_path


class FakeTwitch:
    """Stands in for the Twitch provider: knows a fixed set of channels."""
    def __init__(self, users):
        self.users = {u.lower(): u for u in users}

    def users_by_login(self, logins):
        out = []
        for l in logins:
            u = self.users.get(l.lower().lstrip("@"))
            if u:
                out.append(StreamerHit("twitch", f"id_{u.lower()}", u.lower(), u, False))
        return out

    def search_channels(self, q, n=10):
        return self.users_by_login([q.replace(" ", "")])


def _campaign(name, label, ctype="pay_per_view", rate=1.0, min_payout=0.0, max_payout=300, budget=5000,
              pool=None, threshold=None, clippers=0, platforms=("tiktok", "youtube_shorts"), hint=None, **extra):
    pid = importer.upsert({"name": name, "streamer_label": label, "campaign_type": ctype, "payout_per_1k": rate,
                           "min_payout": min_payout, "max_payout": max_payout, "budget_total": budget,
                           "budget_remaining": budget, "pool_size": pool, "view_threshold": threshold,
                           "allowed_platforms": list(platforms), "clippers_joined": clippers,
                           "channel_hints": {"twitch": {"value": hint or label.replace(" ", "").lower(),
                                                        "source": "page"}},
                           "_sources": {k: "import" for k in ("campaign_type", "payout_per_1k", "min_payout",
                                                               "max_payout", "budget_total", "budget_remaining",
                                                               "pool_size", "view_threshold", "allowed_platforms")}},
                          operator="test")
    if extra:
        importer.set_fields(pid, extra)
    return pid


def _snapshot(sid, viewers, game="Grand Theft Auto VI"):
    gid = db.q1("SELECT id FROM games WHERE name=?", (game,))
    gid = gid["id"] if gid else db.ex("INSERT INTO games(name) VALUES (?)", (game,))
    db.ex("INSERT INTO streamer_snapshots(streamer_id, game_id, is_live, viewers) VALUES (?,?,1,?)", (sid, gid, viewers))
    return gid


# ---- migrations ---------------------------------------------------------------------------------
def test_migration_keeps_existing_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    path = tmp_path / "old.db"
    db.init_db(path, upto=1)                        # a v0.1 database
    sid = db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                   VALUES ('twitch','1','kaicenat','Kai Cenat','allowed')""")
    pid = db.ex("INSERT INTO programs(name, operator, payout_per_1k) VALUES ('Old program','whop',1.0)")
    db.ex("INSERT INTO streamer_programs VALUES (?,?)", (sid, pid))
    db.ex("INSERT INTO logs(level, source, message) VALUES ('info','x','history')")
    assert migrations.current_version(db.connect()) == 1

    db.init_db(path)                                # upgrade
    assert migrations.current_version(db.connect()) == migrations.LATEST
    s = db.q1("SELECT * FROM streamers WHERE id=?", (sid,))
    assert s["login"] == "kaicenat" and s["permission_status"] in ("allowed", "program_member")
    assert db.q1("SELECT * FROM programs WHERE id=?", (pid,))["joined"] == 1
    assert db.q1("SELECT confirmed FROM streamer_programs WHERE program_id=?", (pid,))["confirmed"] == 1
    assert db.q1("SELECT COUNT(*) n FROM logs")["n"] >= 1
    seeded = db.q("SELECT * FROM programs WHERE operator='content_rewards'")
    assert len(seeded) == 12
    assert {a["handle"] for a in db.q("SELECT handle FROM accounts")} >= {"@vira1gta6clips", "@gta6viralclips-v5y",
                                                                          "@gta6.viralclipsss"}
    assert db.get_setting("content_rewards_username") == "grandtheftclipper"
    db.init_db(path)                                # running again changes nothing
    assert len(db.q("SELECT * FROM programs WHERE operator='content_rewards'")) == 12


def test_seed_types_and_rules(fresh_db):
    by = {r["streamer_label"]: r for r in db.q("SELECT * FROM programs WHERE operator='content_rewards'")}
    ppv = {"Dasgasd3m", "BrettLiftsXP", "Lauren Hamden", "TOMO", "KreekCraft", "Dylan Page", "AsianJeff"}
    pools = {"Kai Cenat", "Lacy", "YourRAGE", "Clix"}
    assert {k for k, v in by.items() if v["campaign_type"] == "pay_per_view" and k} == ppv
    assert {k for k, v in by.items() if v["campaign_type"] == "prize_pool"} == pools
    assert "#DylanPage" in json.loads(by["Dylan Page"]["required_tags"])
    assert "streamer_overlay" in json.loads(by["KreekCraft"]["required_onscreen"])
    assert by[None]["name"].startswith("Tokyo Girl")    # music campaign: no streamer


# ---- needs confirming ---------------------------------------------------------------------------
def test_needs_confirming_flags(fresh_db):
    dylan = db.q1("SELECT * FROM programs WHERE streamer_label='Dylan Page'")
    flags = json.loads(dylan["needs_confirming"])
    assert "ends_at" in flags and "pays_au" in flags and "approval_rate" in flags
    assert "payout_per_1k" not in flags and "required_tags" not in flags        # read from the page
    importer.set_fields(dylan["id"], {"pays_au": 1, "ends_at": "2026-11-25 23:59:59"})
    flags2 = json.loads(db.q1("SELECT needs_confirming FROM programs WHERE id=?", (dylan["id"],))["needs_confirming"])
    assert "pays_au" not in flags2 and "ends_at" not in flags2
    # a typed-in program with nothing filled is flagged everywhere
    assert set(needs_confirming({"campaign_type": "pay_per_view", "field_sources": "{}"})) >= {"payout_per_1k", "pays_au"}


def test_flagged_fields_still_count_with_warning(fresh_db):
    sid = db.ex("INSERT INTO streamers(platform, platform_user_id, login, display_name) VALUES ('twitch','x','x','X')")
    prog = db.q1("SELECT * FROM programs WHERE streamer_label='AsianJeff'")
    p = forecast.project(db.q1("SELECT * FROM streamers WHERE id=?", (sid,)), prog, "tiktok")
    assert p["per_clip"][1] > 0                                           # still projected...
    assert any("needs confirming" in w for w in p["warnings"])            # ...with a warning


# ---- importer -----------------------------------------------------------------------------------
PAGE = """Back
GTA6 Clips
Reach
$2,500 AsianJeff Clipping Campaign
Get paid to clip AsianJeff's GTA 6 playthrough beginning Nov 19th https://www.twitch.tv/asianjeff
Streamer
0
CPM
TikTok
Per 1K views
$1.00
Min payout
$10.00
Max payout
$300.00
YouTube
Per 1K views
$1.00
Min payout
$10.00
Max payout
$300.00
Budget
$40 / $2,500
"""


def test_import_page_text(fresh_db):
    p = importer.parse(PAGE, "https://contentrewards.com/c/campaigns/bbd3a963-9a23-457c-bc15-6697971d9902")
    assert p["payout_per_1k"] == 1.0 and p["min_payout"] == 10 and p["max_payout"] == 300
    assert p["budget_remaining"] == 2460 and p["allowed_platforms"] == ["tiktok", "youtube_shorts"]
    assert p["channel_hints"]["twitch"]["value"] == "asianjeff" and p["streamer_label"] == "AsianJeff"
    pid = importer.upsert(p)
    row = db.q1("SELECT * FROM programs WHERE id=?", (pid,))
    assert row["budget_remaining"] == 2460      # updated the seeded campaign, not a duplicate
    assert len(db.q("SELECT 1 FROM programs WHERE external_id='bbd3a963-9a23-457c-bc15-6697971d9902'")) == 1
    importer.set_fields(pid, {"max_payout": 250})
    importer.upsert(importer.parse(PAGE))       # a later import never overwrites your own edit
    assert db.q1("SELECT max_payout FROM programs WHERE id=?", (pid,))["max_payout"] == 250


# ---- discovery & matching (no hard-coded list) ---------------------------------------------------
def test_campaign_streamers_found_and_ranked_automatically(fresh_db):
    from clipfarm.discovery.service import rank_game, ranked_streamers
    pid = _campaign("Zed Clipping", "ZedLive", rate=2.0, hint="zedlive")
    res = matching.match_program(pid, {"twitch": FakeTwitch(["ZedLive"])})
    assert res["linked"] and not res["pending"]
    s = db.q1("SELECT * FROM streamers WHERE login='zedlive'")
    assert s["permission_status"] == "program_member"
    assert "Content Rewards campaign, joined Nov 2026" in s["permission_note"]
    gid = db.ex("INSERT INTO games(name) VALUES ('Grand Theft Auto VI')")
    forecast.project_all()
    rank_game(gid)
    rows = ranked_streamers(gid)
    assert [r["login"] for r in rows] == ["zedlive"]       # ranked with no snapshots and no fixed list
    assert rows[0]["projection"] and rows[0]["payout_score"] > 0


def test_uncertain_matches_are_listed_not_linked(fresh_db):
    kai = db.q1("SELECT id FROM programs WHERE streamer_label='Kai Cenat'")["id"]
    res = matching.match_program(kai, {"twitch": FakeTwitch(["kaicenat"])})
    assert not res["linked"] and res["pending"]
    pend = matching.pending_matches()
    assert pend and pend[0]["candidate_login"] == "kaicenat"
    assert compliance.can_clip(res["pending"][0]["streamer_id"])[0] is False   # not clippable until confirmed
    matching.confirm_match(pend[0]["id"], True)
    assert db.q1("SELECT permission_status FROM streamers WHERE login='kaicenat'")["permission_status"] == "program_member"


def test_streamer_without_campaign_is_never_autoclipped(fresh_db):
    pid = _campaign("Paid", "PaidGuy", rate=2.0)
    matching.match_program(pid, {"twitch": FakeTwitch(["PaidGuy"])})
    free = db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                    VALUES ('twitch','f','freeguy','FreeGuy','allowed')""")
    _snapshot(free, 90000)
    db.ex("INSERT INTO accounts(platform, handle) VALUES ('tiktok','@x')")
    plan = autopilot.plan({"min_projected_aud_per_clip": 0})
    ids = {c["streamer_id"] for c in plan}
    assert free not in ids and ids                    # big audience, but no payout -> never chosen
    assert matching.active_campaigns_for(free) == []


# ---- projections & ranking ---------------------------------------------------------------------
def _linked(name, label, **kw):
    pid = _campaign(name, label, **kw)
    matching.match_program(pid, {"twitch": FakeTwitch([label])})
    sid = db.q1("SELECT id FROM streamers WHERE login=?", (label.lower(),))["id"]
    return pid, sid


def test_ppv_ranks_above_pool_when_it_projects_higher(fresh_db):
    _, ppv = _linked("Per view", "PerView", rate=2.0, min_payout=2, budget=5000)
    _, pool = _linked("Big pool", "PoolGuy", ctype="prize_pool", pool=10000, threshold=750000, clippers=66)
    for s in (ppv, pool):
        _snapshot(s, 20000)
    rows = forecast.project_all()
    order = [r["streamer_id"] for r in rows]
    assert order.index(ppv) < order.index(pool)
    # the order is exactly the projection maths: sorted by A$ per PC-hour = rank value * 60 / minutes per clip
    vals = [r["aud_per_pc_hour"] for r in rows]
    assert vals == sorted(vals, reverse=True)
    mins = forecast.pc_minutes_per_clip()
    for r in rows:
        assert r["aud_per_pc_hour"] == pytest.approx(r["rank_value"] * 60 / mins, abs=0.01)


def test_pool_ranks_first_when_it_genuinely_projects_higher(fresh_db):
    _, tiny = _linked("Tiny per view", "TinyRate", rate=0.05, min_payout=5, budget=100)
    _, pool = _linked("Easy pool", "EasyPool", ctype="prize_pool", pool=50000, threshold=1000, clippers=0)
    for s in (tiny, pool):
        _snapshot(s, 20000)
    rows = forecast.project_all()
    assert rows[0]["streamer_id"] == pool             # ranking follows the numbers, not the campaign type


def test_projection_bands_and_aud(fresh_db):
    _, sid = _linked("Rate", "RateGuy", rate=1.5, min_payout=3.5, max_payout=150, budget=1000)
    p = [r for r in forecast.project_all() if r["streamer_id"] == sid][0]
    lo, li, hi = p["per_clip"]
    assert 0 <= lo <= li <= hi
    assert p["d7"][1] <= p["d30"][1] + 0.01
    assert p["details"]["usd_to_aud"] == 1.53
    db.set_setting("usd_to_aud", 2.0)
    p2 = [r for r in forecast.project_all() if r["streamer_id"] == sid][0]
    assert p2["per_clip"][1] == pytest.approx(li / 1.53 * 2.0, rel=0.02)


# ---- auto-pilot ---------------------------------------------------------------------------------
def test_autopilot_switches_away_from_out_of_budget_and_ended(fresh_db):
    a_prog, a = _linked("Alpha campaign", "Alpha", rate=2.0, min_payout=1, budget=5000)
    b_prog, b = _linked("Beta campaign", "Beta", rate=1.0, min_payout=1, budget=5000)
    for s in (a, b):
        _snapshot(s, 20000)
    db.ex("INSERT INTO accounts(platform, handle) VALUES ('tiktok','@x')")
    db.set_setting("autopilot", {**autopilot.DEFAULTS, "enabled": False, "max_clips_per_day": 4,
                                 "clips_per_streamer_per_day": 4, "min_projected_aud_per_clip": 0})
    first = autopilot.tick()["plan"]
    assert [c["streamer_id"] for c in first] == [a]
    db.ex("UPDATE programs SET budget_remaining=0 WHERE id=?", (a_prog,))
    second = autopilot.tick()
    assert [c["streamer_id"] for c in second["plan"]] == [b]
    assert db.q1("SELECT status FROM programs WHERE id=?", (a_prog,))["status"] == "out_of_budget"
    log = [r["reason"] for r in db.q("SELECT reason FROM autopilot_log ORDER BY id")]
    assert any(r.startswith("Switched from Alpha") and "$1.00/1k vs $2.00/1k" in r for r in log), log
    # an ended campaign is dropped too
    db.ex("UPDATE programs SET ends_at='2000-01-01 00:00:00' WHERE id=?", (b_prog,))
    third = autopilot.tick()
    assert third["plan"] == []
    assert db.q1("SELECT status FROM programs WHERE id=?", (b_prog,))["status"] == "ended"


def test_autopilot_respects_limits(fresh_db):
    for i in range(5):
        _, s = _linked(f"C{i}", f"Streamer{i}", rate=2.0 - i * 0.2, min_payout=1)
        _snapshot(s, 20000)
    db.ex("INSERT INTO accounts(platform, handle) VALUES ('tiktok','@x')")
    plan = autopilot.plan({"max_clips_per_day": 6, "clips_per_streamer_per_day": 4,
                           "max_posts_per_account_per_day": 3, "min_projected_aud_per_clip": 0})
    assert sum(c["clips_per_day"] for c in plan) <= 6
    plan2 = autopilot.plan({"min_projected_aud_per_clip": 10_000})
    assert plan2 == []


# ---- render rules -------------------------------------------------------------------------------
def test_rules_layout_choice():
    layout, notes, flags = rules.choose_layout("crop", ["streamer_overlay"], facecam_known=True)
    assert layout == "blur_fill" and notes
    layout, _, flags = rules.choose_layout("crop", ["streamer_face"], facecam_known=True)
    assert layout == "split" and not flags
    layout, _, flags = rules.choose_layout("auto", ["streamer_face"], facecam_known=False)
    assert layout == "blur_fill" and flags                      # can't guarantee -> flagged for review
    cap = rules.caption_text("Title", "Desc", ["gta6"], "twitch.tv/x", "#ad", ["#DylanPage"])
    assert "#DylanPage" in cap and "#ad" in cap and "Credit: twitch.tv/x" in cap
    assert rules.check_caption(cap, {"tags": ["#DylanPage"], "disclosure": "#ad"}) == []


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_render_applies_campaign_rules(fresh_db):
    from clipfarm.jobs import pipeline
    from clipfarm.jobs.engine import JobContext, enqueue
    pid, sid = _linked("Overlay campaign", "OverlayGuy", rate=1.0)
    importer.set_fields(pid, {"required_tags": ["#OverlayGuy"], "required_onscreen": ["streamer_overlay"],
                              "disclosure": "#ad", "required_credit": "Watch live: {url}"})
    gid = db.ex("INSERT INTO games(name) VALUES ('Grand Theft Auto VI')")
    proj = db.ex("INSERT INTO projects(name, game_id, duration_sec, output_settings, status) VALUES (?,?,?,?,'running')",
                 ("t", gid, 3600, json.dumps({"layout": "crop"})))
    src = db.ex("INSERT INTO sources(streamer_id, kind, platform_id, url, status) VALUES (?, 'community_clip','c','u','ready')",
                (sid,))
    mid = db.ex("INSERT INTO moments(source_id, start_sec, end_sec, score, signals, status) VALUES (?,0,10,0.9,'{}','queued')",
                (src,))
    payload = {"project_id": proj, "moment_id": mid, "media": str(SAMPLE), "media_offset": 0, "streamer_id": sid}
    jid = enqueue("render_moment", payload)
    pipeline.render_moment(payload, JobContext(db.q1("SELECT * FROM jobs WHERE id=?", (jid,))))
    c = db.q1("SELECT * FROM clips WHERE moment_id=?", (mid,))
    assert c["layout"] == "blur_fill"                         # overlay kept in frame despite "crop"
    applied = json.loads(c["rules_applied"])
    assert applied["program_id"] == pid and applied["notes"]
    assert "#OverlayGuy" in c["caption_text"] and "#ad" in c["caption_text"]
    assert "Watch live: twitch.tv/overlayguy" in c["caption_text"]
    out = pipeline.export_clip(c["id"], fresh_db / "exports")
    assert "#OverlayGuy" in Path(out["caption_file"]).read_text(encoding="utf-8")


# ---- posting safety & submission checklist --------------------------------------------------------
def _clip(sid, prog, h="hash1"):
    return db.ex("""INSERT INTO clips(streamer_id, program_id, file_path, title, content_hash, review_status)
                    VALUES (?,?,'x.mp4','A clip',?, 'approved')""", (sid, prog, h))


def test_submission_checklist_after_posting_and_no_double_submit(fresh_db):
    from clipfarm.posting import submissions as subs
    pid, sid = _linked("Paid", "PaidGuy", rate=2.0)
    acct = db.q1("SELECT id FROM accounts WHERE platform='tiktok'")["id"]
    cid = _clip(sid, pid)
    res = subs.record_posted(cid, acct, "https://www.tiktok.com/@vira1gta6clips/video/1")
    item = res["submission"]
    assert item["label"] == "Submit to Paid on Test" or item["label"].startswith("Submit to Paid on")
    assert item["post_url"].endswith("/video/1") and item["status"] == "todo"
    assert len(subs.checklist("todo")) == 1
    subs.mark_submitted(item["id"])
    with pytest.raises(subs.AlreadySubmitted):
        subs.mark_submitted(item["id"])
    subs.create_submission_item(res["post_id"])                    # can't create a second item for the post
    assert db.q1("SELECT COUNT(*) n FROM submissions WHERE post_id=?", (res["post_id"],))["n"] == 1
    with pytest.raises(sqlite3.IntegrityError):
        db.ex("INSERT INTO submissions(post_id, program_id) VALUES (?,?)", (res["post_id"], pid))
    cr = db.q1("SELECT id FROM programs WHERE streamer_label='TOMO'")["id"]
    db.ex("UPDATE clips SET program_id=? WHERE id=?", (cr, cid))
    assert subs.checklist_item(item["id"])["label"].startswith("Submit to")


def test_posting_safety(fresh_db):
    from clipfarm.posting import submissions as subs
    pid, sid = _linked("Paid", "PaidGuy", rate=2.0, platforms=("tiktok",))
    tiktok = db.q1("SELECT id FROM accounts WHERE platform='tiktok'")["id"]
    insta = db.q1("SELECT id FROM accounts WHERE platform='instagram'")["id"]
    c1 = _clip(sid, pid, "h1")
    subs.record_posted(c1, tiktok, "https://tiktok.com/v/1")
    with pytest.raises(subs.PostingBlocked):                        # same clip, same account
        subs.record_posted(c1, tiktok, "https://tiktok.com/v/2")
    c2 = _clip(sid, pid, "h1")                                      # same video re-rendered as a new clip
    with pytest.raises(subs.PostingBlocked):
        subs.schedule(c2, [tiktok])
    with pytest.raises(subs.PostingBlocked, match="doesn't pay"):   # campaign doesn't allow Instagram
        subs.schedule(_clip(sid, pid, "h9"), [insta])
    db.ex("UPDATE accounts SET max_posts_per_day=2 WHERE id=?", (tiktok,))
    subs.record_posted(_clip(sid, pid, "h2"), tiktok, "https://tiktok.com/v/3")
    with pytest.raises(subs.PostingBlocked, match="limit"):
        subs.record_posted(_clip(sid, pid, "h3"), tiktok, "https://tiktok.com/v/4")


def test_schedule_spreads_posts(fresh_db):
    from datetime import datetime
    from clipfarm.posting import submissions as subs
    pid, sid = _linked("Paid", "PaidGuy", rate=2.0, platforms=("tiktok",))
    tiktok = db.q1("SELECT id FROM accounts WHERE platform='tiktok'")["id"]
    now = datetime(2026, 11, 20, 8, 0)
    times = [datetime.fromisoformat(subs.schedule(_clip(sid, pid, f"s{i}"), [tiktok], now)[0]["scheduled_at"])
             for i in range(5)]
    per_day = {}
    for t in times:
        per_day[t.date()] = per_day.get(t.date(), 0) + 1
    assert max(per_day.values()) <= 3
    times.sort()
    for a, b in zip(times, times[1:]):
        if a.date() == b.date():
            assert (b - a).total_seconds() >= 90 * 60


# ---- keys survive uninstall / reinstall --------------------------------------------------------------
def test_keys_restored_from_vault_after_reinstall(fresh_db):
    secrets.set_secret("twitch_client_id", "tw-client-123456")
    secrets.set_secret("twitch_client_secret", "tw-secret-abcdef")
    secrets.set_secret("account:1", {"refresh_token": "1//refresh-xyz-123456", "expires_at": 0})
    assert vault.vault_path().exists()
    # Uninstall + reinstall: the program folder is replaced and Credential Manager may lose entries,
    # but %APPDATA%\ClipFarm\vault.dat stays.
    for n in ("twitch_client_id", "twitch_client_secret", "account:1"):
        secrets._kc_del(n)
    res = secrets.restore([1])
    assert secrets.get_secret("twitch_client_id") == "tw-client-123456"
    assert secrets.get_secret("account:1", as_json=True)["refresh_token"] == "1//refresh-xyz-123456"
    if secrets.keyring:
        assert res["restored_to_keychain"] == 3
        assert secrets._kc_get("twitch_client_secret") == "tw-secret-abcdef"


def test_vault_backs_up_keys_from_older_installs(fresh_db):
    if not secrets.keyring:
        pytest.skip("no OS keychain in this environment")
    secrets._kc_set("youtube_api_key", "AIzaOldInstallKey000000000000000000000")   # only in the keychain
    assert "youtube_api_key" not in vault.load()
    assert secrets.restore()["backed_up_to_vault"] >= 1
    assert vault.load()["youtube_api_key"].startswith("AIzaOld")


def test_vault_is_encrypted_and_tied_to_this_user(fresh_db):
    secrets.set_secret("llm_api_key", "sk-very-secret-value-123")
    raw = vault.vault_path().read_bytes()
    assert b"sk-very-secret" not in raw and raw.startswith(vault.MAGIC)
    if not isinstance(vault.protector(), vault.KeyFileProtector):
        pytest.skip("DPAPI: other-user test needs a second Windows account")
    other = vault.KeyFileProtector(fresh_db / "other.key")            # a different PC / account
    with pytest.raises(Exception):
        other.unprotect(raw[len(vault.MAGIC):])


def test_export_import_with_password(fresh_db):
    blob = vault.export_bundle({"twitch_client_id": "abc123456"}, {"accent": "#fff"}, "correct horse")
    assert b"abc123456" not in blob
    assert vault.import_bundle(blob, "correct horse")["secrets"]["twitch_client_id"] == "abc123456"
    with pytest.raises(ValueError, match="Wrong password"):
        vault.import_bundle(blob, "wrong password")
    with pytest.raises(ValueError):
        vault.export_bundle({}, {}, "short")


def test_wipe_removes_keys_and_vault(fresh_db):
    secrets.set_secret("kick_client_id", "kick-123456")
    secrets.wipe_all()
    assert not vault.vault_path().exists()
    assert secrets.get_secret("kick_client_id") is None


# ---- no secret in logs, the database, config files or crash output -----------------------------------
def test_no_secret_leaks(fresh_db, monkeypatch):
    import logging
    import requests
    from clipfarm import health, redact
    from clipfarm.jobs import engine as eng
    from clipfarm.main import _RedactingStream
    s_key, s_secret, s_token = "AIzaSyLEAKTEST000000000000000000000000", "leak-secret-987654", "ya29.leaktoken555"
    secrets.set_secret("youtube_api_key", s_key)
    secrets.set_secret("twitch_client_secret", s_secret)
    secrets.set_secret("account:7", {"access_token": s_token, "refresh_token": "1//leakrefresh", "expires_at": 0})
    # a failing job whose exception contains a key in a URL (requests does this on connection errors)
    @eng.handler("leaky")
    def leaky(payload, ctx):
        raise requests.exceptions.ConnectionError(
            f"Max retries exceeded with url: /youtube/v3/search?part=snippet&key={s_key} secret={s_secret}")
    jid = eng.enqueue("leaky", {})
    db.ex("UPDATE jobs SET max_attempts=1 WHERE id=?", (jid,))
    e = eng.Engine(workers=1, render_workers=0)
    e._run(e._claim("1=1"))
    db.log("error", "test", f"token was Bearer {s_token} and secret {s_secret}")
    logging.getLogger("clipfarm.test").error("client_secret=%s", s_secret)
    # health checks with the network down (messages are built from exceptions)
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError(f"https://x/?key={s_key}")))
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError(f"https://x/?key={s_key}")))
    health.run_all(["twitch", "youtube", "kick", "llm"])
    health.write_diagnostics()
    # crash output / engine.log
    log_file = fresh_db / "engine.log"
    with open(log_file, "w", encoding="utf-8") as f:
        _RedactingStream(f).write(f"Traceback ... key={s_key} {s_secret} {s_token}\n")

    blobs = []
    db.connect().execute("PRAGMA wal_checkpoint(FULL)")
    for p in fresh_db.rglob("*"):
        if p.is_file() and p.suffix != ".key" and p.name != "vault.dat":
            blobs.append((p, p.read_bytes()))
    for p, b in blobs:
        for s in (s_key, s_secret, s_token, "1//leakrefresh"):
            assert s.encode() not in b, f"{s} leaked into {p}"
    for table in ("settings", "logs", "jobs", "health_checks"):
        dump = json.dumps(db.q(f"SELECT * FROM {table}"))
        for s in (s_key, s_secret, s_token):
            assert s not in dump, f"{s} leaked into {table}"
    assert redact.redact(f"x {s_secret} y") == "x [REDACTED] y"


def test_settings_refuse_secrets(fresh_db):
    with pytest.raises(ValueError):
        db.set_setting("twitch_client_secret", "oops")
    with pytest.raises(ValueError):
        db.set_setting("account:3", {"access_token": "x"})


# ---- setup health ---------------------------------------------------------------------------------
def test_health_runs_offline_without_crashing(fresh_db, monkeypatch):
    import requests
    from clipfarm import health
    secrets.set_secret("twitch_client_id", "cid12345")
    secrets.set_secret("twitch_client_secret", "sec12345")
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectionError()))
    res = {r["id"]: r for r in health.run_all()}
    assert set(res) >= {"engine", "storage", "keys", "ffmpeg", "twitch", "youtube", "kick", "llm", "campaigns",
                        "tiktok", "instagram", "x", "whisper"}
    assert res["engine"]["status"] == "green"
    assert res["twitch"]["status"] == "yellow" and "offline" in res["twitch"]["message"]
    assert res["llm"]["status"] == "yellow" and "template" in res["llm"]["message"]
    for r in res.values():
        assert r["status"] in ("green", "yellow", "red") and r["message"]
    assert (fresh_db / "diagnostics.txt").exists()
    assert health.summary()["red"] == sum(1 for r in res.values() if r["status"] == "red")


def test_netcheck_messages():
    import requests
    from clipfarm.netcheck import explain_response

    def resp(code, body, headers=None):
        r = requests.Response()
        r.status_code, r._content, r.headers = code, body.encode(), headers or {}
        return r
    assert "Reconnect" in explain_response("YouTube", resp(400, '{"error":"invalid_grant"}'), fix_reconnect="x").message
    assert explain_response("YouTube", resp(403, '{"reason":"quotaExceeded"}')).status == "yellow"
    assert "Client ID or Secret" in explain_response("Twitch", resp(403, '{"message":"invalid client secret"}')).message
    assert explain_response("Twitch", resp(429, "rate limit", {"Retry-After": "30"})).status == "yellow"
    assert explain_response("Twitch", resp(200, "{}")) is None
