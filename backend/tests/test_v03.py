"""v0.3 tests: "Import this page", Whop/Content Rewards safety, match-on-click, the grouped streamer list,
project auto-fill, the 5-item menu, and migrations. No network."""
import json
import re
import sqlite3
from pathlib import Path

import pytest

from clipfarm import db, migrations
from clipfarm.campaigns import autofill, grouping, importer, matching

from test_v02 import PAGE, _campaign, _snapshot

ROOT = Path(__file__).resolve().parents[2]
AJ_URL = "https://contentrewards.com/c/campaigns/bbd3a963-9a23-457c-bc15-6697971d9902"


def _streamer(login, platform="twitch", display=None, status="allowed"):
    return db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                    VALUES (?,?,?,?,?)""", (platform, f"id_{platform}_{login}", login, display or login, status))


def _no_llm(monkeypatch):
    import clipfarm.llm.client as c

    def boom(*a, **k):
        raise c.LLMUnavailable("off")
    monkeypatch.setattr(c, "complete_json", boom)


# ---- 1. Import this page ----------------------------------------------------------------------------
def test_import_reads_only_the_page_it_is_given(fresh_db, monkeypatch):
    """The importer parses the text handed to it and makes no network request of any kind."""
    _no_llm(monkeypatch)
    import requests
    import socket

    def no_net(*a, **k):
        raise AssertionError("import tried to use the network")
    monkeypatch.setattr(requests, "get", no_net)
    monkeypatch.setattr(requests.Session, "request", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)
    s = importer.run_import(importer.PageSource(importer.PageInput(url=AJ_URL, text=PAGE)))
    assert s.total == 1 and (s.updated or s.unchanged)


def test_import_has_no_background_or_scheduled_path():
    """No job, timer or startup hook calls the importer; only the two user-triggered routes do."""
    src = ROOT / "backend" / "clipfarm"
    callers = []
    for f in src.rglob("*.py"):
        t = f.read_text(encoding="utf-8")
        if re.search(r"importer\.(run_import|PageSource|PasteSource)", t) and f.name != "importer.py":
            callers.append(f.name)
    assert callers == ["api.py"]
    api = (src / "api.py").read_text(encoding="utf-8")
    assert len(re.findall(r"importer\.run_import\(", api)) == 2
    for f in ("jobs/pipeline.py", "jobs/engine.py", "main.py", "campaigns/autopilot.py"):
        t = (src / f).read_text(encoding="utf-8")
        assert "import_page" not in t and "run_import" not in t


def test_nothing_fetches_or_clicks_on_whop_or_content_rewards():
    """No Python makes HTTP requests to Whop/Content Rewards; no injected script clicks or submits."""
    for f in (ROOT / "backend" / "clipfarm").rglob("*.py"):
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r"(requests|httpx|urllib)\S*\.(get|post|request|urlopen)\(([^)]*)\)", t):
            assert not re.search(r"whop|contentrewards", m.group(3), re.I), f"{f.name}: {m.group(0)}"
    scripts = [ROOT / "app" / "src-tauri" / "src" / "campaign_browser.js",
               ROOT / "app" / "src-tauri" / "src" / "campaign_browser.rs"]
    for f in scripts:
        t = f.read_text(encoding="utf-8")
        assert not re.search(r"\.click\(\)|\.submit\(\)|requestSubmit|dispatchEvent\(\s*new\s+MouseEvent", t), f.name
        assert "__TAURI__" not in t or f.suffix == ".rs"
    js = scripts[0].read_text(encoding="utf-8")
    # Only runs on a button press -- no timers, observers or load hooks sending page data.
    assert not re.search(r"setInterval|setTimeout\([^)]*send|MutationObserver|addEventListener\(\s*['\"]load", js)
    assert js.count("sendPage(") == 2      # definition + the one button handler


def test_import_updates_existing_and_reports_changes(fresh_db, monkeypatch):
    _no_llm(monkeypatch)
    first = importer.run_import(importer.PasteSource(PAGE, AJ_URL))
    assert first.total == 1
    changed = PAGE.replace("$1.00", "$1.50").replace("$1 / 1K", "$1.50 / 1K")
    s = importer.run_import(importer.PasteSource(changed, AJ_URL))
    assert len(s.updated) == 1
    assert any(c.startswith("Rate: $1.00/1K → $1.50/1K") for c in s.updated[0]["changes"]), s.updated[0]["changes"]
    assert re.match(r"Imported 1 campaign: 0 new, 1 updated\.", s.text())
    assert len(db.q("SELECT 1 FROM programs WHERE external_id='bbd3a963-9a23-457c-bc15-6697971d9902'")) == 1


def test_llm_values_not_on_the_page_are_flagged(fresh_db, monkeypatch):
    import clipfarm.llm.client as c
    page = "Bob's Clipping Campaign\nStreamer: BobLive\nPays $3 per 1,000 views\nEnds Dec 12\nTikTok, Instagram"
    monkeypatch.setattr(c, "complete_json", lambda *a, **k: {
        "name": "Bob's Clipping Campaign", "streamer_label": "BobLive", "payout_per_1k": 3,
        "max_payout": 999, "allowed_platforms": ["tiktok", "instagram_reels"], "pays_au": True,
        "ends_at": "2026-12-12"})
    s = importer.run_import(importer.PageSource(importer.PageInput(url="https://contentrewards.com/c/discover",
                                                                  text=page)))
    assert len(s.new) == 1
    item = s.new[0]
    flagged = set(item["needs_confirming"])
    assert "max_payout" in flagged and "pays_au" in flagged        # invented by the model -> needs confirming
    assert "payout_per_1k" not in flagged and "allowed_platforms" not in flagged   # backed by the page
    row = db.q1("SELECT * FROM programs WHERE id=?", (item["id"],))
    assert row["payout_per_1k"] == 3


def test_import_list_page_imports_every_campaign(fresh_db, monkeypatch):
    _no_llm(monkeypatch)
    ids = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
           "bbd3a963-9a23-457c-bc15-6697971d9902"]
    links = [{"href": f"https://contentrewards.com/c/campaigns/{ids[0]}",
              "text": "Alpha GTA 6 Clipping\n$2.00 / 1K\nTikTok\n12\n$1.5K pool"},
             {"href": f"https://contentrewards.com/c/campaigns/{ids[1]}",
              "text": "Beta Clips\n$0.80 / 1K\nInstagram\nEnded"},
             {"href": AJ_URL, "text": "$2,500 AsianJeff Clipping Campaign\n$1.00 / 1K\nTikTok"}]
    s = importer.run_import(importer.PageSource(importer.PageInput(url="https://contentrewards.com/c/discover",
                                                                  text="Discover", links=links)))
    assert s.total == 3 and len(s.new) == 2
    assert len(s.ended) == 1 and s.ended[0]["name"].startswith("Beta")
    assert "Imported 3 campaigns: 2 new" in s.text() and "1 ended" in s.text()


def test_official_api_slot_exists():
    src = importer.ApiSource()
    assert src.name == "official_api"
    with pytest.raises(NotImplementedError):
        src.campaigns()


# ---- 2. Match on click ------------------------------------------------------------------------------
def test_click_auto_assigns_confident_match(fresh_db):
    pid = _campaign("Zed Clipping", "ZedLive", rate=2.0, hint="zedlive")
    sid = _streamer("zedlive", display="ZedLive")
    r = matching.match_streamer(sid)
    assert r["state"] == "matched" and r["auto"] and r["note"] == "Matched to Zed Clipping"
    assert db.q1("SELECT confirmed FROM streamer_programs WHERE streamer_id=? AND program_id=?", (sid, pid))["confirmed"] == 1
    assert matching.match_streamer(sid)["auto"] is False          # already confirmed: just shown


def test_click_asks_once_for_unsure_match_and_remembers(fresh_db):
    kreek = db.q1("SELECT id FROM programs WHERE streamer_label='KreekCraft'")["id"]
    sid = _streamer("kreekcraft", display="KreekCraft")
    q = matching.match_streamer(sid)
    assert q["state"] == "ask" and q["options"][0]["program_id"] == kreek
    assert "KreekCraft" in q["question"]
    matching.answer(sid, kreek)
    again = matching.match_streamer(sid)
    assert again["state"] == "matched" and again["campaign"]["id"] == kreek     # never asked again
    # "None of these" is remembered too
    other = _streamer("kreekcraft", platform="kick", display="KreekCraft")
    assert matching.match_streamer(other)["state"] == "ask"
    matching.answer(other, None)
    assert matching.match_streamer(other)["state"] == "none"


# ---- 3. Grouped list ------------------------------------------------------------------------------
def _ranked(*sids):
    return [db.q1("SELECT *, 0.0 profit_score FROM streamers WHERE id=?", (s,)) for s in sids]


def test_grouping_sections_and_sorting(fresh_db):
    db.ex("INSERT INTO accounts(platform, handle) VALUES ('tiktok','@t')")
    lo = _campaign("Low", "LowGuy", rate=0.5, hint="lowguy")
    hi = _campaign("High", "HighGuy", rate=4.0, hint="highguy")
    un = _campaign("Unjoined", "NewGuy", rate=3.0, hint="newguy")
    db.ex("UPDATE programs SET joined=0 WHERE id=?", (un,))
    a, b, c, d = (_streamer("lowguy"), _streamer("highguy"), _streamer("newguy"), _streamer("nobody"))
    for s in (a, b, c, d):
        _snapshot(s, 20000)
    for s in (a, b):
        matching.match_streamer(s)
    db.set_setting("autopilot", {"min_projected_aud_per_clip": 0.0})
    g = grouping.grouped(None, _ranked(a, b, c, d))
    assert [r["id"] for r in g["joined"]] == [b, a]              # sorted by projected earnings
    assert [r["id"] for r in g["worth_joining"]] == [c]
    assert g["worth_joining"][0]["reason"].startswith("Pays $3/1K")
    assert [r["id"] for r in g["no_campaign"]] == [d]
    pj = g["joined"][0]["projection"]
    assert pj["per_clip"][0] <= pj["per_clip"][1] <= pj["per_clip"][2]   # low-likely-high for the hover
    assert lo and hi


@pytest.mark.parametrize("fields,why", [
    ({"status": "ended"}, "ended"),
    ({"budget_remaining": 0}, "Budget"),
    ({"pays_au": 0}, "Australian"),
    ({"ends_at": "2000-01-01 00:00:00"}, "ended"),
])
def test_worth_joining_excludes_dead_campaigns(fresh_db, fields, why):
    pid = _campaign("Maybe", "MaybeGuy", rate=5.0, hint="maybeguy")
    db.ex("UPDATE programs SET joined=0 WHERE id=?", (pid,))
    for k, v in fields.items():
        db.ex(f"UPDATE programs SET {k}=? WHERE id=?", (v, pid))
    sid = _streamer("maybeguy")
    _snapshot(sid, 20000)
    db.set_setting("autopilot", {"min_projected_aud_per_clip": 0.0})
    g = grouping.grouped(None, _ranked(sid))
    assert not g["worth_joining"]
    assert g["worth_joining_left_out"] and why.lower() in g["worth_joining_left_out"][0]["excluded_reason"].lower()


def test_worth_joining_respects_minimum_per_clip(fresh_db):
    pid = _campaign("Tiny", "TinyGuy", rate=0.01, hint="tinyguy")
    db.ex("UPDATE programs SET joined=0 WHERE id=?", (pid,))
    sid = _streamer("tinyguy")
    db.set_setting("autopilot", {"min_projected_aud_per_clip": 5.0})
    g = grouping.grouped(None, _ranked(sid))
    assert not g["worth_joining"] and "minimum" in g["worth_joining_left_out"][0]["excluded_reason"]


# ---- 4. New project auto-fill -----------------------------------------------------------------------
def test_new_project_autofill_limited_to_allowed_platforms(fresh_db):
    pid = _campaign("Tok Only", "TokGuy", rate=2.0, hint="tokguy", platforms=("tiktok",))
    importer.set_fields(pid, {"required_tags": ["#gta6", "#tokguy"], "disclosure": "#ad"})
    sid = _streamer("tokguy")
    matching.match_streamer(sid)
    f = autofill.fill([sid], ["tiktok", "youtube_shorts", "instagram_reels"])
    assert f["rewards_account"]["username"] == "grandtheftclipper"
    s = f["streamers"][0]
    assert s["campaign"]["id"] == pid and s["platforms"] == ["tiktok"]
    assert {a["handle"] for a in s["accounts"]} == {"@vira1gta6clips"}            # TikTok account only
    assert s["skipped_platforms"] == ["youtube_shorts", "instagram_reels"]
    assert s["rules"]["hashtags"] == ["#gta6", "#tokguy"] and s["rules"]["disclosure"] == "#ad"
    assert f["summary"] == "1 streamer · 1 campaign · posting to TikTok · Whop: grandtheftclipper"
    out = autofill.output_settings({"platforms": f["platforms"], "clips_per_day": 5}, f)
    assert out["per_streamer"][str(sid)]["program_id"] == pid and out["rewards_account"] == "grandtheftclipper"
    assert out["quotas"][str(sid)]["clips_per_day"] == 5


def test_new_accounts_are_picked_up_and_last_project_remembered(fresh_db):
    db.ex("INSERT INTO accounts(platform, handle) VALUES ('tiktok','@second')")
    sid = _streamer("plain")
    f = autofill.fill([sid], ["tiktok"])
    assert {a["handle"] for a in f["streamers"][0]["accounts"]} == {"@vira1gta6clips", "@second"}
    autofill.remember({"duration_sec": 7200, "clips_per_day": 3, "platforms": ["tiktok"], "junk": 1})
    d = autofill.defaults()
    assert d["duration_sec"] == 7200 and d["clips_per_day"] == 3 and d["platforms"] == ["tiktok"]
    assert "junk" not in d


# ---- 5. Menu --------------------------------------------------------------------------------------
def test_menu_has_at_most_five_items_and_old_screens_reachable():
    app = (ROOT / "app" / "src" / "App.tsx").read_text(encoding="utf-8")
    nav = re.search(r"const NAV[^=]*=\s*\[(.*?)\];", app, re.S).group(1)
    items = re.findall(r'\[\s*"(\w+)"\s*,\s*"([^"]+)"', nav)
    assert [label for _, label in items] == ["Home", "New Project", "My Clips", "Earnings", "Settings"]
    # Every v0.2 screen still exists and is mounted somewhere.
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app" / "src").rglob("*.tsx"))
    for comp in ("Review", "Submissions", "Campaigns", "Health", "Logs", "Programs", "Earnings", "Setup"):
        assert re.search(rf"<{comp}\b", src), f"{comp} is no longer reachable"


# ---- migrations -----------------------------------------------------------------------------------
def test_v4_migration_keeps_data_and_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFARM_DATA_DIR", str(tmp_path))
    path = tmp_path / "v3.db"
    db.init_db(path, upto=3)
    sid = _streamer("oldguy")
    pid = db.q1("SELECT id FROM programs WHERE streamer_label='KreekCraft'")["id"]
    db.ex("INSERT INTO streamer_programs(streamer_id, program_id, confirmed) VALUES (?,?,1)", (sid, pid))
    n_prog = db.q1("SELECT COUNT(*) n FROM programs")["n"]
    db.init_db(path)
    assert migrations.current_version(db.connect()) == migrations.LATEST >= 4
    assert db.q1("SELECT COUNT(*) n FROM programs")["n"] == n_prog
    assert db.q1("SELECT decision FROM match_decisions WHERE streamer_id=? AND program_id=?",
                 (sid, pid))["decision"] == "accepted"
    assert matching.match_streamer(sid)["state"] == "matched"      # a v0.2 confirmation is never re-asked
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(programs)")}
    assert {"last_changes", "import_source", "last_imported_at"} <= cols
    assert json.loads(db.q1("SELECT field_sources FROM programs WHERE id=?", (pid,))["field_sources"]) is not None
