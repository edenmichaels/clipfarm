"""v0.6: virality score, projected earnings per clip, campaign rule flags + one-click fixes, Editor API."""
from __future__ import annotations

import json

import pytest

from clipfarm import db
from clipfarm.edit import project
from clipfarm.scoring import clipscore, profit, rules_check, virality


def _words(start: float, end: float, gap: float = 0.3, text="no way bro that was insane what") -> list[dict]:
    out, t, toks = [], start, text.split()
    i = 0
    while t < end - 0.3:
        out.append({"word": toks[i % len(toks)], "start": round(t, 2), "end": round(t + 0.3, 2)})
        t += 0.3 + gap
        i += 1
    return out


def _edl(a=100.0, b=124.0, words=None, peak=None, hook="NO WAY he did that", mtype="Funny", score=0.8):
    e = project.blank({"path": "/x.mp4", "w": 1920, "h": 1080, "fps": 30, "start": 0, "end": 1000}, words if words is not None
                      else _words(a, b), a, b)
    e["hook"]["text"] = hook
    e["moment"].update(type=mtype, peak=a + 16 if peak is None else peak, score=score)
    e["music"]["enabled"] = False
    return e


# ---- virality ---------------------------------------------------------------------------------------
def test_virality_rewards_a_strong_hook():
    good = virality.score(_edl())
    slow = virality.score(_edl(words=_words(104.5, 124), hook=None))
    assert good["score"] > slow["score"] + 8
    assert good["parts"]["hook"] > slow["parts"]["hook"]
    assert "slow start" in slow["reason"].lower() or "hook" in slow["reason"].lower()


def test_virality_penalises_dead_air():
    talky = virality.score(_edl(words=_words(100, 124, gap=0.2)))
    dead = virality.score(_edl(words=_words(100, 124, gap=2.2)))
    assert talky["parts"]["pacing"] > dead["parts"]["pacing"] + 20
    assert talky["score"] > dead["score"]
    assert "dead air" in dead["reason"]


def test_virality_prefers_the_right_length():
    right = virality.score(_edl(100, 128, peak=118))
    long = virality.score(_edl(100, 190, peak=118))
    assert right["score"] > long["score"]
    assert "long" in long["reason"] or "after the payoff" in long["reason"]


def test_virality_reason_mentions_the_payoff_time():
    v = virality.score(_edl(peak=114))
    assert "payoff at 0:14" in v["reason"] and 0 <= v["score"] <= 100


def test_trends_raise_relevance():
    e = _edl(words=_words(100, 124, text="the heist went wrong vice city bank"))
    assert virality.score(e, trends=["heist"])["parts"]["relevance"] > virality.score(e)["parts"]["relevance"]


# ---- profit -----------------------------------------------------------------------------------------
def _clip_with_program(rate=2.0, min_views=0, max_payout=500.0):
    n = db.q1("SELECT COUNT(*) n FROM streamers")["n"] + 1
    sid = db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                   VALUES ('twitch',?,?,'S','allowed')""", (f"u{n}", f"s{n}"))
    pid = db.ex("""INSERT INTO programs(name, operator, payout_per_1k, min_views, max_payout, joined)
                   VALUES ('P','whop',?,?,?,1)""", (rate, min_views, max_payout))
    db.ex("INSERT INTO streamer_programs(streamer_id, program_id, confirmed) VALUES (?,?,1)", (sid, pid))
    return db.ex("INSERT INTO clips(streamer_id, program_id, title) VALUES (?,?,'c')", (sid, pid))


def test_view_multiplier_is_monotonic():
    xs = [profit.view_multiplier(v) for v in (10, 30, 50, 70, 90, 99)]
    assert xs == sorted(xs) and abs(profit.view_multiplier(50) - 1.0) < 0.01


def test_profit_grows_with_virality_and_payout(fresh_db):
    c = db.q1("SELECT * FROM clips WHERE id=?", (_clip_with_program(rate=1.0),))
    lo = profit.project(c, 30)
    hi = profit.project(c, 90)
    assert hi["aud"][1] > lo["aud"][1] and hi["expected_views"] > lo["expected_views"]
    c2 = db.q1("SELECT * FROM clips WHERE id=?", (_clip_with_program(rate=4.0),))
    assert profit.project(c2, 90)["aud"][1] > hi["aud"][1]


def test_rank_for_posting_uses_earnings_and_rules(fresh_db):
    clips = [{"id": 1, "virality": 95, "profit_likely": 2.0, "rule_flags": "[]"},
             {"id": 2, "virality": 70, "profit_likely": 9.0, "rule_flags": "[]"},
             {"id": 3, "virality": 99, "profit_likely": 50.0, "rule_flags": json.dumps([{"severity": "error"}])}]
    assert [c["id"] for c in clipscore.rank_for_posting(clips)] == [2, 1, 3]


def test_scores_are_stored_and_follow_edits(fresh_db):
    cid = _clip_with_program()
    project.create(cid, _edl())
    a = db.q1("SELECT virality, profit_likely, virality_reason FROM clips WHERE id=?", (cid,))
    assert a["virality"] and a["profit_likely"] is not None and a["virality_reason"]
    project.update(cid, lambda e: {**e, "hook": {**e["hook"], "text": None}, "range": [e["range"][0] - 6, e["range"][1]]},
                   "worse")
    b = db.q1("SELECT virality FROM clips WHERE id=?", (cid,))
    assert b["virality"] < a["virality"]
    project.undo(cid)
    assert db.q1("SELECT virality FROM clips WHERE id=?", (cid,))["virality"] == a["virality"]


# ---- rules ------------------------------------------------------------------------------------------
def _ruled():
    e = _edl()
    e["rules"].update(tags=["#DylanPage"], disclosure="#ad", onscreen=["streamer_overlay"], no_music=True,
                      platforms=["youtube_shorts"], credit="twitch.tv/dylan")
    e["layout"]["keyframes"] = [{"t": e["range"][0], "layout": "crop", "path": [[0, 0.5]]}]
    e["music"].update(enabled=True, path="/m.wav")
    e["texts"] = {"tiktok": {"title": "t", "description": "d", "hashtags": ["gta6"]}}
    e["credit_hidden"] = True
    return e


def test_rule_violations_are_flagged():
    ids = {f["id"] for f in rules_check.check(_ruled())}
    assert {"overlay", "music", "platform", "hashtags:tiktok", "disclosure:tiktok", "credit"} <= ids
    assert rules_check.check(_edl()) == []


@pytest.mark.parametrize("fid", ["overlay", "music", "platform", "hashtags:tiktok", "disclosure:tiktok", "credit"])
def test_each_one_click_fix_works(fid):
    e = _ruled()
    fixed = rules_check.fix(e, fid)
    assert fid not in {f["id"] for f in rules_check.check(fixed)}
    assert e == _ruled()                     # pure


def test_fix_all_clears_every_fixable_flag():
    e, done = rules_check.fix_all(_ruled())
    assert not [f for f in rules_check.check(e) if f["fix"]] and len(done) >= 6
    assert e["texts"]["tiktok"]["hashtags"][0] == "#DylanPage" and e["export"]["preset"] == "shorts"


def test_length_fix_keeps_the_payoff():
    e = _edl(100, 260, peak=240)
    e["export"]["preset"] = "x"
    assert any(f["id"] == "length" for f in rules_check.check(e))
    f = rules_check.fix(e, "length")
    assert not any(x["id"] == "length" for x in rules_check.check(f))
    assert f["range"][0] <= 240 <= f["range"][1]


def test_fix_my_clip_cleans_up_and_keeps_the_payoff():
    from clipfarm.edit import timeline as T
    from clipfarm.scoring import fixclip
    words = [{"word": "um", "start": 104.0, "end": 104.3}] + _words(104.5, 112) + _words(116, 126)
    e = _edl(100, 140, words=words, peak=120, hook=None)
    e["hook"]["options"] = ["He did NOT see that coming", "Wait for it"]
    f, done = fixclip.fix_my_clip(e)
    assert {"removed filler words", "removed silences", "trimmed the slow start", "cut the ending after the payoff",
            "added a hook"} <= set(done)
    assert f["range"][0] <= 120 <= f["range"][1] and T.duration(f) < T.duration(e)
    assert virality.score(f)["score"] > virality.score(e)["score"]
    assert fixclip.fix_my_clip(f)[1] == []                 # idempotent


def test_fix_my_clip_never_destroys_a_clip():
    from clipfarm.scoring import fixclip
    e = _edl(100, 102.5, words=[], peak=None, hook=None)
    f, done = fixclip.fix_my_clip(e)
    assert f["range"] == e["range"]
