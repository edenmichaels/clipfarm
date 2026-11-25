"""v0.8.8: preset bar, variations (cheap previews, kept ones become clips) and live dots in the streamer pickers.
Renders are real for one preset check; jobs, Twitch, Kick and YouTube are mocked."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from clipfarm import db
from clipfarm.edit import compile as ec, project, styles, variations as V
from clipfarm.editing import hooks
from clipfarm.live import status as LS
from test_v08_bugs import _grey_source
from test_v08_styles import _styled

APP = Path(__file__).resolve().parents[2] / "app" / "src"


def _no_llm(monkeypatch):
    from clipfarm.llm.client import LLMUnavailable

    def down(*a, **k):
        raise LLMUnavailable("off")
    monkeypatch.setattr(hooks, "complete_json", down)


def _fixed_crops(e: dict) -> bool:
    """No panning: every shot has one crop (a single path point), changes between shots are hard cuts."""
    return all(len(k.get("path") or [[0, 0.5]]) == 1 for k in e["layout"]["keyframes"])


# ---- 1A. presets ------------------------------------------------------------------------------------------------
def test_six_presets_follow_the_rules(fresh_db, tmp_path):
    ps = styles.presets()
    assert [p["name"] for p in ps] == ["Streamer Corner", "Split Screen", "Bold Box", "Clean Minimal", "Hype",
                                       "Facecam Focus"]
    assert all(p["description"] and not p["audio"]["music"] for p in ps)
    by = {p["key"]: p for p in ps}
    assert by["bold_box"]["captions"]["box"] and by["bold_box"]["title"]["box"] == "color_box"
    assert by["hype"]["captions"]["highlight_color"] == "3DFF5A" and by["hype"]["captions"]["outline"] == 12
    assert by["hype"]["effects"]["emoji"] and by["hype"]["title"]["color"] == "E53935"
    assert not by["clean_minimal"]["captions"]["highlight"] and by["clean_minimal"]["title"]["box"] == "plain"
    assert by["split_screen"]["layout"]["mode"] == "split" and by["split_screen"]["captions"]["anchor"] == "seam"
    assert by["facecam_focus"]["layout"]["mode"] == "full_reaction"
    src = tmp_path / "s.mp4"
    _grey_source(src, 9)
    for p in ps:
        e = _styled(src, p["key"])
        assert _fixed_crops(e) and e["music"]["enabled"] is False, p["key"]
        if p["key"] == "facecam_focus":
            assert {k["layout"] for k in e["layout"]["keyframes"]} == {"focus"}


def test_bold_box_draws_a_static_rounded_box_and_hype_a_green_word(fresh_db, tmp_path):
    from clipfarm.editing import captions as C
    words = [{"word": w, "start": 0.5 + i * 0.4, "end": 0.8 + i * 0.4} for i, w in enumerate("no way he did that".split())]
    tr = C.build_track(words, tmp_path / "b.ass", {**C.preset("bold_pop").as_dict(), "box": True, "outline_px": 0},
                       hook="Title", title_box="color_box", title_color="7C4DFF", vocab=False)
    ass = (tmp_path / "b.ass").read_text(encoding="utf-8")
    assert "\\p1}m " in ass and "TitleColorBox" in ass and "&H00FF4D7C" in ass.upper()
    assert not re.search(r"\\(t\(|fad|move|fscy)", ass)                          # nothing animates
    boxes = [ln for ln in ass.splitlines() if "CapBox" in ln and "Dialogue" in ln]
    assert len(boxes) == len(tr.captions)                                       # one box per caption, its whole time
    hy = C.build_track(words, tmp_path / "h.ass", {**C.preset("hype_yellow").as_dict(), "outline_px": 12, "emoji": True},
                       vocab=False)
    assert "3DFF5A" in C.preset("hype_yellow").highlight and hy.events


# ---- 1B. variations ------------------------------------------------------------------------------------------------
def _clip_with_project(tmp_path, monkeypatch) -> int:
    src = tmp_path / "s.mp4"
    _grey_source(src, 12)
    e = _styled(src, "streamer_corner")
    e["captions"]["words"] = [{"word": w, "start": 1.0 + i * 0.4, "end": 1.3 + i * 0.4} for i, w in enumerate(
        "okay okay. no way he drove into the river! bro what was that. I told you the bridge was safe".split())]
    e["hook"]["text"] = "He did NOT see that coming"
    cid = db.ex("INSERT INTO clips(title, review_status, hook_text) VALUES ('River', 'approved', 'He did NOT see that coming')")
    project.create(cid, e)
    return cid


@pytest.fixture
def jobs(monkeypatch):
    got = []
    monkeypatch.setattr("clipfarm.jobs.engine.enqueue",
                        lambda kind, payload, *a, **k: got.append((kind, payload)) or len(got))
    return got


@pytest.mark.parametrize("n,preset", [(4, "streamer_corner"), (10, "mix"), (6, "hype")])
def test_n_variations_unique_hooks_and_preset_rules(fresh_db, tmp_path, monkeypatch, jobs, n, preset):
    _no_llm(monkeypatch)
    cid = _clip_with_project(tmp_path, monkeypatch)
    made = V.make(cid, n, preset)
    assert len(made) == n
    hooks_ = [m["hook"] for m in made]
    assert len({hooks.norm(h) for h in hooks_}) == n and "He did NOT see that coming" not in hooks_
    assert all("new hook" in m["changes"] for m in made) and all(m["label"] for m in made)
    assert [k for k, _ in jobs] == ["render_variation"] * n                        # previews only
    for row in db.q("SELECT * FROM variations WHERE clip_id=?", (cid,)):
        e = json.loads(row["edl"])
        assert _fixed_crops(e) and e["music"]["enabled"] is False
        assert row["style_key"] in styles.PRESET_KEYS
        if preset != "mix":
            assert row["style_key"] == preset
        assert all(z.get("kind") == "punch" for z in e.get("zooms", []))          # punch-ins only (instant cuts)


def test_previews_are_cheap_and_only_kept_ones_get_full_renders(fresh_db, tmp_path, monkeypatch, jobs):
    _no_llm(monkeypatch)
    cid = _clip_with_project(tmp_path, monkeypatch)
    made = V.make(cid, 3, "streamer_corner")
    qualities = []

    def fake_render(edl, out, cache, quality="export", music=True, **kw):
        qualities.append((quality, music))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"x" * 100)
        return {"path": str(out), "thumb": None}
    monkeypatch.setattr(ec, "render", fake_render)
    for m in made:
        V.render_preview(m["id"], tmp_path / f"v{m['id']}.mp4")
    assert qualities == [("proxy", False)] * 3
    jobs.clear()
    kept = V.keep([made[0]["id"], made[2]["id"]])
    assert [k for k, _ in jobs] == ["export_edit", "export_edit"]                 # full renders: kept ones only
    for k in kept:
        c = db.q1("SELECT * FROM clips WHERE id=?", (k["clip_id"],))
        assert c["variant_of"] == cid and c["review_status"] == "approved" and c["hook_text"]
        assert project.get(k["clip_id"]) is not None
    assert V.discard(cid) == 1
    st = {r["id"]: r["status"] for r in db.q("SELECT id, status FROM variations")}
    assert sorted(st.values()) == ["discarded", "kept", "kept"]
    assert not (tmp_path / f"v{made[1]['id']}.mp4").exists()
    assert V.keep([made[1]["id"]]) == []                                          # discarded: can't be kept later


def test_variation_labels_say_what_differs(fresh_db, tmp_path, monkeypatch, jobs):
    _no_llm(monkeypatch)
    cid = _clip_with_project(tmp_path, monkeypatch)
    labels = [m["label"] for m in V.make(cid, 8, "mix")]
    words = " ".join(labels).lower()
    assert any(x in words for x in ("tighter start", "more setup", "earlier end"))
    assert any(x in words for x in ("highlight", "title"))
    assert all(lbl.endswith("new hook") for lbl in labels)


# ---- 2. live dots ------------------------------------------------------------------------------------------------
class Twitch:
    def __init__(self, live=None, fail=False):
        self.calls, self.live, self.fail = [], live or {}, fail

    def live_status(self, ids):
        self.calls.append(list(ids))
        if self.fail:
            raise RuntimeError("Twitch down")
        assert len(ids) <= 100
        return {u: s for u, s in self.live.items() if u in ids}


def _streamers(n: int, platform="twitch") -> list[int]:
    return [db.ex("INSERT INTO streamers(platform, platform_user_id, login, display_name) VALUES (?,?,?,?)",
                  (platform, f"{platform}{i}", f"s{i}", f"S{i}")) for i in range(n)]


def test_live_offline_unknown_and_one_twitch_call_per_100(fresh_db):
    LS.clear_cache()
    ids = _streamers(100)
    tw = Twitch(live={"twitch3": {"viewer_count": 45200, "started_at": "2026-11-25T01:00:00Z",
                                  "game_name": "Grand Theft Auto VI", "title": "heists"}})
    st = LS.for_streamers(ids, {"twitch": tw})
    assert len(tw.calls) == 1 and len(tw.calls[0]) == 100                       # ONE call for the whole list
    assert st[ids[3]]["state"] == "live" and st[ids[3]]["viewers"] == 45200 and st[ids[3]]["game"] == "Grand Theft Auto VI"
    assert st[ids[0]] == {"state": "offline"}
    LS.for_streamers(ids, {"twitch": tw})
    assert len(tw.calls) == 1                                                   # cached for 55 s
    LS.clear_cache()
    more = ids + _streamers(50, "twitch") if False else ids + [db.ex(
        "INSERT INTO streamers(platform, platform_user_id, login, display_name) VALUES ('twitch', ?, ?, ?)",
        (f"x{i}", f"x{i}", f"X{i}")) for i in range(50)]
    tw2 = Twitch()
    LS.for_streamers(more, {"twitch": tw2})
    assert [len(c) for c in tw2.calls] == [100, 50]
    LS.clear_cache()
    bad = LS.for_streamers(ids[:5], {"twitch": Twitch(fail=True)})
    assert all(v == {"state": "unknown", "note": "status unavailable"} for v in bad.values())


def test_kick_and_youtube_checks(fresh_db, monkeypatch):
    LS.clear_cache()
    k = _streamers(2, "kick")

    class KickHttp:
        def get(self, path, headers, params):
            assert path == "livestreams" and ("broadcaster_user_id", "kick0") in params
            return {"data": [{"broadcaster_user_id": "kick0", "viewer_count": 900, "started_at": "2026-11-25T00:00:00Z",
                              "category": {"name": "Just Chatting"}, "stream_title": "hi"}]}
    kick = type("K", (), {"http": KickHttp(), "_headers": lambda self: {}})()
    st = LS.for_streamers(k, {"kick": kick})
    assert st[k[0]]["state"] == "live" and st[k[0]]["game"] == "Just Chatting" and st[k[1]]["state"] == "offline"
    y = _streamers(2, "youtube")
    db.ex("UPDATE streamers SET platform_user_id='UCaaa' WHERE id=?", (y[0],))
    db.ex("UPDATE streamers SET platform_user_id='UCbbb' WHERE id=?", (y[1],))
    calls = []

    class YT:
        def _get(self, path, params):
            calls.append(path)
            assert path != "search"                                             # never search.list (100 units)
            if path == "playlistItems":
                return {"items": [{"contentDetails": {"videoId": "v" + params["playlistId"][-3:]}}]}
            return {"items": [{"id": "vaaa", "liveStreamingDetails": {"actualStartTime": "2026-11-25T00:00:00Z",
                                                                       "concurrentViewers": "1234"}, "snippet": {"title": "t"}},
                              {"id": "vbbb", "liveStreamingDetails": {"actualStartTime": "x", "actualEndTime": "y"}}]}
    st = LS.for_streamers(y, {"youtube": YT()})
    assert st[y[0]]["state"] == "live" and st[y[0]]["viewers"] == 1234 and st[y[1]]["state"] == "offline"
    assert calls.count("videos") == 1
    LS.clear_cache()
    db.set_setting("live_status", {"youtube_daily_units": 1})                  # budget used up: unknown, no calls
    calls.clear()
    st = LS.for_streamers(y, {"youtube": YT()})
    assert all(v["state"] == "unknown" for v in st.values()) and calls == []


def test_picker_polls_every_minute_and_stops_when_closed():
    src = (APP / "components" / "LiveDot.tsx").read_text(encoding="utf-8")
    assert "POLL_MS = 60_000" in src and "setInterval(load, POLL_MS)" in src
    assert "clearInterval(timer.current)" in src and "if (!open || !key) return;" in src
    lw = (APP / "pages" / "LiveWatcher.tsx").read_text(encoding="utf-8")
    assert "/live/streamers" in lw and "<LiveDot st={s.status} />" in lw        # v0.8.9: rows carry the live dot
    for f in ("components/StreamerSections.tsx", "components/StreamerTable.tsx"):
        t = (APP / f).read_text(encoding="utf-8")
        assert "<LiveDot" in t and "liveRank" in t                              # dot + live first, everywhere
