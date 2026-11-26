"""v0.8.9: chat-first detection (no video/vision), auto-pilot on the Live Watcher."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from clipfarm import db
from clipfarm.campaigns import autopilot
from clipfarm.live import buffer as B, chat as C, judge as J, pilot as P, watcher as W

ROOT = Path(__file__).resolve().parents[1] / "clipfarm"


def _baseline(st: C.ChatStats, t0: float, per_10s_people: int, minutes: int = 5) -> float:
    """Normal chat: `per_10s_people` different people laughing every 10 s, plus chatter."""
    n = 0
    for k in range(minutes * 6):
        t = t0 + k * 10
        for i in range(per_10s_people):
            st.add(t + i * 0.1, f"norm{(k * per_10s_people + i) % 400}", "LUL")
        st.add(t + 5, f"talk{k}", "what game is this")
        st.stats(t + 9.9)
        n += 1
    return t0 + minutes * 60


def test_matching_handles_case_repeats_and_spacing():
    t = C.Tagger(C.DEFAULT_TAGS)
    assert t.match("CLIPPPP")["clip"] and t.match("c l i p")["clip"] and t.match("someone clip that")["clip"]
    assert t.match("kekw")["laugh"] and t.match("💀💀💀")["laugh"] and t.match("NAHHHHH")["shock"]
    assert t.match("??????")["shock"] and not t.match("eclipse tonight") and not t.match("good morning chat")


def test_one_spammer_cannot_trigger_but_five_people_can():
    st = C.ChatStats(C.DEFAULT_TAGS)
    now = _baseline(st, 1_000_000.0, 0)
    for i in range(60):                                       # one person spamming CLIP IT 60 times
        st.add(now + i * 0.15, "spammer", "CLIP IT KEKW")
    assert not st.stats(now + 9.5)["trigger"]
    st2 = C.ChatStats(C.DEFAULT_TAGS)
    now = _baseline(st2, 1_000_000.0, 0)
    for i in range(5):
        st2.add(now + i, f"viewer{i}", "clip it")
    s = st2.stats(now + 9.5)
    assert s["unique"] == 5 and s["trigger"]


def test_bots_and_commands_are_ignored():
    st = C.ChatStats(C.DEFAULT_TAGS, login="kai")
    for i, u in enumerate(["nightbot", "streamelements", "kai", "a", "b"]):
        st.add(100 + i, u, "!clip KEKW" if u in ("a", "b") else "KEKW")
    assert st.counts(0, 200)["unique"] == 0


def test_baselines_are_per_channel_busy_vs_small():
    busy, small = C.ChatStats(C.DEFAULT_TAGS), C.ChatStats(C.DEFAULT_TAGS)
    tb = _baseline(busy, 0.0, 20)                              # 20 people laughing every 10 s is normal here
    ts = _baseline(small, 0.0, 0)                              # a quiet chat
    for i in range(8):                                         # the same burst: 8 people laughing
        busy.add(tb + i, f"x{i}", "KEKW")
        small.add(ts + i, f"x{i}", "KEKW")
    assert not busy.stats(tb + 9.5)["trigger"]                 # nothing special for a busy chat
    assert small.stats(ts + 9.5)["trigger"]                    # a big deal for a small one


def test_trigger_settings_are_per_streamer(fresh_db):
    st = C.ChatStats(C.DEFAULT_TAGS, {"min_unique": 10, "multiplier": 3})
    now = _baseline(st, 0.0, 0)
    for i in range(6):
        st.add(now + i, f"v{i}", "LMAO")
    assert not st.stats(now + 9.5)["trigger"]
    db.set_setting("chat_tags:7", {"laugh": ["kaiDance"], "clip": [], "shock": []})
    tags = W.tags_for(7)
    assert tags["laugh"] == ["kaiDance"] and tags["clip"] == C.DEFAULT_TAGS["clip"]        # empty -> defaults
    assert C.Tagger(tags).match("kaiDance kaiDance")["laugh"]


def test_clip_requests_weigh_most():
    st = C.ChatStats(C.DEFAULT_TAGS)
    for i in range(3):
        st.add(i, f"a{i}", "clip it")
        st.add(i, f"b{i}", "W")
    c = st.counts(0, 10)
    assert c["weighted"] == 3 * 3 + 3 * 1 and C.WEIGHTS["clip"] > C.WEIGHTS["laugh"] > C.WEIGHTS["shock"]


def _fire(w: W.Watch, now: float) -> dict:
    w.last_trigger = 0
    for i in range(12):
        w.chat.add(now - 4 + i * 0.3, f"p{i}", "KEKW clip it")
    assert w.check(now, W.DEFAULTS) is None and w.episode                 # opened
    out = None
    t = now
    while out is None and t < now + 30:
        t += 1
        out = w.check(t, W.DEFAULTS)
    return out


def test_clip_starts_before_the_spike_using_the_learned_delay(fresh_db):
    w = W.Watch({"id": 3, "login": "kai", "display_name": "Kai"})
    now = _baseline(w.chat, 1_000_000.0, 0)
    trig = _fire(w, now)
    assert trig["start"] == pytest.approx(trig["t"] - 20)
    assert trig["end"] - trig["t"] <= 20 and "people" in trig["evidence"]
    # viewer clips teach the delay: chat spiked 12 s after the clip's start, twice
    assert W.learn_delay(3, 500.0, [512.0, 900.0]) == 12.0
    assert W.learn_delay(3, 1000.0, [1012.0]) == 12.0 and W.delay_for(3) == 12.0
    w2 = W.Watch({"id": 3, "login": "kai", "display_name": "Kai"})
    now = _baseline(w2.chat, 2_000_000.0, 0)
    trig = _fire(w2, now)
    assert trig["start"] == pytest.approx(trig["t"] - 12) and trig["delay"] == 12.0


def test_no_frames_or_vision_model_anywhere_in_detection():
    assert not (ROOT / "highlights" / "visual.py").exists()
    assert "images" not in inspect.signature(J.judge).parameters
    from clipfarm.llm import client
    assert "images" not in inspect.signature(client.complete_json).parameters
    assert not hasattr(B, "frames") and not hasattr(B, "audio_peak")
    from clipfarm.highlights import detect, events, run
    assert "visual" not in detect.DEFAULT_WEIGHTS and not hasattr(events, "vision_tags")
    assert "visual_fn" not in inspect.signature(run.find_highlights).parameters if hasattr(run, "find_highlights") else True
    for p in [*(ROOT / "live").glob("*.py"), *(ROOT / "highlights").glob("*.py"), ROOT / "jobs" / "pipeline.py"]:
        src = p.read_text(encoding="utf-8")
        assert "vision_tags" not in src and "highlights.visual" not in src and "import visual" not in src, p


# ---- auto-pilot on the Live Watcher ---------------------------------------------------------------------------------
def _setup_pilot(n=4):
    sids = []
    for i in range(n):
        sids.append(db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                             VALUES ('twitch', ?, ?, ?, 'allowed')""", (f"u{i}", f"s{i}", f"S{i}")))
    plan = [{"streamer_id": sids[0], "program_id": 1, "program": "A", "rate": 1.0, "campaign_type": "prize_pool",
             "aud_per_pc_hour": 9.0},
            {"streamer_id": sids[1], "program_id": 2, "program": "B", "rate": 1.5, "campaign_type": "pay_per_view",
             "aud_per_pc_hour": 3.0},
            {"streamer_id": sids[2], "program_id": 3, "program": "C", "rate": 2.0, "campaign_type": "pay_per_view",
             "aud_per_pc_hour": 5.0},
            {"streamer_id": sids[3], "program_id": 4, "program": "D", "rate": 1.0, "campaign_type": "pay_per_view",
             "aud_per_pc_hour": 1.0}]
    db.set_setting("autopilot_plan", plan)
    autopilot.save_settings(enabled=True, max_streamers=2) if hasattr(autopilot, "save_settings") else \
        db.set_setting("autopilot", {**autopilot.settings(), "enabled": True, "max_streamers": 2})
    return sids


def test_autopilot_watches_best_live_streamers_and_moves_on(fresh_db):
    s0, s1, s2, s3 = _setup_pilot()
    live = {s0, s1, s2, s3}
    st = lambda ids: {i: {"state": "live" if i in live else "offline", "viewers": 1000} for i in ids}  # noqa: E731
    assert P.step(None, status_fn=st, cap=2) == [s2, s1]               # pay-per-view first, then projected A$
    live.discard(s2)                                                    # S2 goes offline -> next best live one
    assert P.step(None, status_fn=st, cap=2) == [s1, s3]
    logs = [r["reason"] for r in db.q("SELECT reason FROM autopilot_log ORDER BY id")]
    assert any(x.startswith("Started watching S2 (live, 1,000 viewers, $2.00/1K)") for x in logs)
    assert any(x.startswith("Stopped watching S2 (offline) · Kept 0 of 0 triggers") for x in logs)
    assert W.settings()["auto_streamers"] == [s1, s3]
    db.set_setting("autopilot", {**autopilot.settings(), "campaigns": [4]})           # only campaign D
    assert P.step(None, status_fn=st, cap=2) == [s3]


def test_autopilot_capacity_drops_under_pressure_and_run_hours():
    assert P.capacity(3, cpu=20, disk_free_gb=100) == (3, None)
    assert P.capacity(3, cpu=95, disk_free_gb=100)[0] == 2
    assert P.capacity(3, cpu=20, disk_free_gb=2)[0] == 1
    import time
    t = time.mktime((2026, 11, 25, 23, 30, 0, 0, 0, -1))
    assert P.in_run_hours({"start": "22:00", "end": "07:00"}, t)
    assert not P.in_run_hours({"start": "09:00", "end": "17:00"}, t) and P.in_run_hours(None, t)


def test_autopilot_streamers_use_autopilot_min_score(fresh_db):
    db.set_setting("autopilot", {**autopilot.settings(), "min_score": 8.5, "clips_per_streamer_per_hour": 2})
    m = W.Manager()
    w = W.Watch({"id": 1, "login": "k", "display_name": "K"}, auto=True)
    assert m.cfg_for(w, W.settings())["threshold"] == 8.5 and m.cfg_for(w, W.settings())["per_hour"] == 2
