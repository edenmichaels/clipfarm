"""v0.7 (Stage 4): AI hooks + per-platform texts, SFX pack, auto effects, B-roll, brand templates, variants,
one learning system (ratings + keep/delete/export + views -> highlight AND virality weights), export handoff."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipfarm import db
from clipfarm.edit import brand, compile as ec, effects, project, variants
from clipfarm.edit import timeline as T
from test_v05 import _audio, _edl, _tone_source


@pytest.fixture
def no_llm(monkeypatch):
    import clipfarm.editing.hooks as H
    from clipfarm.llm.client import LLMUnavailable

    def boom(*a, **k):
        raise LLMUnavailable("offline")
    monkeypatch.setattr(H, "complete_json", boom)


# ---- hooks + titles ---------------------------------------------------------------------------------
def test_hook_options_and_platform_texts_always_carry_campaign_tags(no_llm):
    from clipfarm.editing.hooks import generate_options
    o = generate_options("bro no way. he drove into the vault! cops everywhere", "KreekCraft", "GTA VI", "Funny",
                         ["#KreekCraft", "Rockstar"], "#ad", "twitch.tv/kreekcraft")
    assert 3 <= len(o["hooks"]) <= 5 and len(set(h.lower() for h in o["hooks"])) == len(o["hooks"])
    assert all(len(h.split()) <= 9 for h in o["hooks"])
    for plat in ("tiktok", "youtube_shorts", "instagram_reels", "x"):
        tx = o["texts"][plat]
        assert {"kreekcraft", "rockstar"} <= set(tx["hashtags"]), plat
        assert "#ad" in tx["description"] and "twitch.tv/kreekcraft" in tx["description"]
    assert "shorts" in o["texts"]["youtube_shorts"]["hashtags"]


def test_hook_options_from_llm(monkeypatch):
    import clipfarm.editing.hooks as H
    monkeypatch.setattr(H, "complete_json", lambda *a, **k: {
        "hooks": ["He drove INTO the vault", "Cops had no chance", "Wait for the end", "Is this legal?"],
        "tiktok": {"title": "vault speedrun", "hashtags": ["gta6", "heist"]},
        "youtube_shorts": {"title": "KreekCraft drives into the vault", "description": "GTA 6 heist", "hashtags": ["gta6"]},
        "instagram_reels": {"caption": "the vault 😂", "hashtags": ["gta6"]}, "x": {"text": "the vault"}})
    o = H.generate_options("x", "KreekCraft", "GTA VI", "Funny", ["#KreekCraft"])
    assert o["llm"] and o["hooks"][0] == "He drove INTO the vault" and len(o["hooks"]) == 5
    assert o["texts"]["tiktok"]["hashtags"][0] == "kreekcraft" and "heist" in o["texts"]["tiktok"]["hashtags"]


# ---- SFX --------------------------------------------------------------------------------------------
def test_sfx_pack_is_bundled_cc0_and_only_licensed_files_are_mixed(fresh_db, tmp_path):
    from clipfarm.music import sfx
    items = sfx.listing()
    assert len(items) >= 8 and all("CC0" in s["licence"] for s in items)
    assert all(sfx.licensed(s["path"]) for s in items)
    stray = tmp_path / "song.wav"
    stray.write_bytes(b"RIFF")
    assert not sfx.licensed(stray)
    e = _edl(Path("/x.mp4"), [], 0, 10)
    e["sfx"] = [{"id": "boom", "path": items[1]["path"], "t": 2.0}, {"id": "x", "path": str(stray), "t": 3.0}]
    spec = ec.spec_for(e, tmp_path / "o.mp4", tmp_path / "b.mp4")
    assert [Path(s["path"]).name for s in spec.sfx] == [Path(items[1]["path"]).name]


# ---- auto effects -----------------------------------------------------------------------------------
def _moment_edl(mtype="Action/Explosion"):
    words = [{"word": w, "start": 10 + i * 0.5, "end": 10.4 + i * 0.5} for i, w in enumerate(
        "okay we go in now and no way bro look at that".split())]
    e = _edl(Path("/x.mp4"), words, 8, 30)
    e["moment"].update(type=mtype, peak=20.0)
    return e


def test_auto_effects_match_the_moment_and_rerun_cleanly(fresh_db):
    e = _moment_edl()
    e["zooms"] = [{"t": 12.0, "kind": "punch", "amount": 0.1, "dur": 0.5}]          # added by hand
    assert not [z for z in effects.auto_effects(e)["zooms"] if z.get("auto")]       # v0.8.5: zoom cuts off by default
    e["pacing"] = {"zoom_cuts": True}
    a = effects.auto_effects(e)
    # v0.8.3: amounts are "+N %" (0.24 = 124 %), not scale factors -- 1.24 used to render at 224 %
    assert any(z["t"] == 20.0 and 0.2 < z["amount"] < 0.3 for z in a["zooms"])
    kinds = {o["kind"] for o in a["overlays"]}
    assert {"flash", "emoji"} <= kinds and "shake" not in kinds                       # never a shake
    assert any(s["id"] == "boom" and s["t"] == 20.0 for s in a["sfx"]) and any(s["id"] == "whoosh" for s in a["sfx"])
    assert any(z["t"] == 12.0 and not z.get("auto") for z in a["zooms"])
    b = effects.auto_effects(a)
    assert len(b["zooms"]) == len(a["zooms"]) and len(b["sfx"]) == len(a["sfx"])          # no duplicates
    f = effects.auto_effects(_moment_edl("Funny"), emoji=False, sfx=False)
    assert not f["sfx"] and not any(o["kind"] == "emoji" for o in f["overlays"])
    assert not any(o["kind"] == "flash" for o in f["overlays"])


def test_auto_zoom_snaps_to_the_beat(fresh_db):
    e = _moment_edl("Funny")
    e["music"].update(enabled=True, path="/m.m4a", zoom_beats=[T.to_out(e, 20.0) + 0.2])
    e["pacing"] = {"zoom_cuts": True}
    z = next(z for z in effects.auto_effects(e, sfx=False)["zooms"] if z["amount"] > 0.1)
    assert abs(T.to_out(e, z["t"]) - (T.to_out(e, 20.0) + 0.2)) < 0.01


def test_broll_from_your_library_only(fresh_db, tmp_path):
    lib = tmp_path / "broll"
    lib.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360:d=1", "-frames:v", "1",
                    str(lib / "police_cops.png")], check=True)
    db.set_setting("music", {"broll_dir": str(lib)})
    words = [{"word": w, "start": 10 + i * 0.5, "end": 10.4 + i * 0.5} for i, w in enumerate("the cops are here".split())]
    e = _edl(Path("/x.mp4"), words, 8, 20)
    br = effects.auto_broll(e)
    assert len(br) == 1 and br[0]["path"].endswith("police_cops.png") and br[0]["keyword"] == "cops"
    e["broll"] = br + [{"path": str(tmp_path / "elsewhere.png"), "t": 12, "dur": 1}]
    spec = ec.spec_for(e, tmp_path / "o.mp4", tmp_path / "b.mp4")
    assert len(spec.broll) == 1


# ---- brand templates --------------------------------------------------------------------------------
def test_brand_template_validation_assignment_and_apply(fresh_db, tmp_path):
    logo = tmp_path / "logo.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x00ff00:s=200x200:d=1", "-frames:v", "1",
                    str(logo)], check=True)
    with pytest.raises(ValueError):
        brand.save("Bad", {"logo_path": str(tmp_path / "nope.png")})
    with pytest.raises(ValueError):
        brand.save("Bad", {"logo_pos": "middle"})
    t = brand.save("TikTok look", {"caption_template": "hype_yellow", "logo_path": str(logo), "progress_color": "#ff0055",
                                   "outro_text": "Follow for more", "credit_pos": "bottom"})
    aid = db.ex("INSERT INTO accounts(platform, handle) VALUES ('tiktok','@me')")
    brand.assign(aid, t["id"])
    assert brand.for_accounts([999, aid])["name"] == "TikTok look"
    e = brand.apply(_edl(Path("/x.mp4"), [], 0, 10), brand.for_accounts([aid]))
    assert e["captions"]["template"] == "hype_yellow" and e["brand"]["progress_color"] == "FF0055"
    spec = ec.spec_for(e, tmp_path / "o.mp4", tmp_path / "b.mp4")
    assert spec.logo["path"] == str(logo) and spec.cards[0]["text"] == "Follow for more" and spec.credit_pos == "bottom"
    assert spec.progress_color == "FF0055"


def test_brand_renders_logo_card_and_bar(fresh_db, tmp_path):
    src = tmp_path / "s.mkv"
    words = _tone_source(src, 12)
    logo = tmp_path / "logo.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x00ff00:s=200x200:d=1", "-frames:v", "1",
                    str(logo)], check=True)
    t = brand.save("B", {"logo_path": str(logo), "logo_pos": "tl", "logo_opacity": 1.0, "logo_size": 150,
                         "intro_text": "HELLO", "card_bg": "0000FF", "progress_color": "FF0000"})
    e = brand.apply(_edl(src, words, 3, 9), t)
    e["progress_bar"] = True
    e["captions"]["enabled"] = False
    res = ec.render(e, tmp_path / "o.mp4", tmp_path / "cache", "export")

    def frame(at):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", res["path"], "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)
    f0, f1 = frame(0.3), frame(4.0)
    assert f0[1500, 540, 2] > 180 and f0[1500, 540, 0] < 80              # blue intro card
    assert f1[300, 140, 1] > 180 and f1[300, 140, 0] < 90                 # green logo top-left
    assert f1[1914, 200, 0] > 180 and f1[1914, 200, 1] < 90               # red progress bar (2/3 through)


# ---- variants ---------------------------------------------------------------------------------------
def test_variants_differ_and_remember_the_original(fresh_db, tmp_path):
    cid = db.ex("INSERT INTO clips(title) VALUES ('Vault')")
    e = _moment_edl("Funny")
    e["source"]["end"] = 60
    e["hook"].update(text="A", options=["A", "B hook", "C hook"])
    e["layout"]["keyframes"] = [{"t": 8, "layout": "blur_fill", "path": [[0, .5]]}]
    project.create(cid, e)
    made = variants.make(cid, 3)
    assert [m["label"] for m in made] == ["B", "C", "D"]
    rows = {r["id"]: r for r in db.q("SELECT id, variant_of, variant_label, title FROM clips WHERE variant_of=?", (cid,))}
    assert len(rows) == 3 and all(r["title"].endswith(f"[{r['variant_label']}]") for r in rows.values())
    eb, ec_, ed = (project.get(m["clip_id"])["edl"] for m in made)
    assert eb["hook"]["text"] != "A" and T.duration(eb) < T.duration(e)
    assert ec_["layout"]["keyframes"][0]["layout"] != "blur_fill"
    assert T.duration(ed) > T.duration(e)
    assert db.q1("SELECT virality FROM clips WHERE id=?", (made[0]["clip_id"],))["virality"] is not None


# ---- learning ---------------------------------------------------------------------------------------
def test_learning_reweights_virality_from_feedback_and_signals(fresh_db):
    from clipfarm.highlights import learning
    from clipfarm.scoring.virality import DEFAULT_WEIGHTS
    sid = db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                   VALUES ('twitch','9','s9','S9','allowed')""")
    rng = np.random.default_rng(0)
    for i in range(70):
        hook = float(rng.uniform(0, 100))
        parts = {k: float(rng.uniform(30, 70)) for k in DEFAULT_WEIGHTS} | {"hook": hook}
        cid = db.ex("INSERT INTO clips(streamer_id, title, virality_parts) VALUES (?,?,?)", (sid, f"c{i}", json.dumps(parts)))
        good = hook > 50
        if i % 2:
            learning.record(cid, 1 if good else -1)
        else:
            db.ex("INSERT INTO clip_signals(clip_id, kind) VALUES (?,?)", (cid, "export" if good else "delete"))
    res = learning.refit(f"streamer:{sid}")
    assert res["fitted"] and res["ratings"] == 70
    vw = res["virality_weights"]
    assert vw["hook"] > DEFAULT_WEIGHTS["hook"] + 0.05 and abs(sum(vw.values()) - 1) < 0.01
    from clipfarm.scoring import virality
    assert virality.weights_for(sid, None) == vw
    learning.reset(f"streamer:{sid}")
    assert virality.weights_for(sid, None) == DEFAULT_WEIGHTS


def test_predicted_vs_actual(fresh_db):
    from clipfarm.highlights import learning
    aid = db.ex("INSERT INTO accounts(platform, handle) VALUES ('tiktok','@me')")
    for i, (v, exp, act) in enumerate([(90, 4000, 9000), (60, 1500, 1400), (40, 700, 300), (75, 2500, 3000), (30, 500, 200)]):
        cid = db.ex("INSERT INTO clips(title, virality, expected_views) VALUES (?,?,?)", (f"c{i}", v, exp))
        pid = db.ex("INSERT INTO posts(clip_id, account_id, status, scheduled_at) VALUES (?,?,'posted',datetime('now'))", (cid, aid))
        db.ex("INSERT INTO post_metrics(post_id, views) VALUES (?,?)", (pid, act))
    r = learning.predicted_vs_actual()
    assert r["n"] == 5 and r["median_ratio"] == pytest.approx(0.93, abs=0.01) and r["score_vs_views_correlation"] > 0.8


# ---- export handoff + auto-pilot finish -------------------------------------------------------------
def test_export_hands_platform_texts_to_post_and_submit(fresh_db):
    from clipfarm.jobs.pipeline import apply_texts
    cid = db.ex("INSERT INTO clips(title) VALUES ('old')")
    e = _edl(Path("/x.mp4"), [], 0, 10)
    e["rules"].update(tags=["#KreekCraft"], disclosure="#ad", credit="twitch.tv/kreekcraft")
    e["texts"] = {"youtube_shorts": {"title": "Shorts title", "description": "desc", "hashtags": ["gta6", "shorts"]},
                  "tiktok": {"title": "TT title", "description": "", "hashtags": ["fyp"]}}
    apply_texts(cid, e, "shorts")
    c = db.q1("SELECT title, caption_text FROM clips WHERE id=?", (cid,))
    assert c["title"] == "Shorts title"
    for must in ("#KreekCraft", "#ad", "twitch.tv/kreekcraft", "#shorts"):
        assert must in c["caption_text"]


def test_autopilot_finish_adds_hooks_texts_brand_effects_and_cleans_up(fresh_db, no_llm, tmp_path):
    from clipfarm.jobs.pipeline import finish_auto_edit
    logo = tmp_path / "l.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=100x100:d=1", "-frames:v", "1",
                    str(logo)], check=True)
    t = brand.save("Mine", {"logo_path": str(logo), "caption_template": "clean_white"})
    aid = db.ex("INSERT INTO accounts(platform, handle, brand_template_id) VALUES ('tiktok','@me',?)", (t["id"],))
    e = _moment_edl("Action/Explosion")
    e["rules"]["tags"] = ["#KreekCraft"]
    e["hook"]["text"] = None
    out = finish_auto_edit(e, "okay we go in now and no way bro look at that", "KreekCraft", "GTA VI",
                           "Action/Explosion", ["#KreekCraft"], None, None, [aid])
    assert len(out["hook"]["options"]) >= 3 and out["hook"]["text"]
    assert "kreekcraft" in out["texts"]["tiktok"]["hashtags"]
    assert out["brand"]["name"] == "Mine" and out["captions"]["template"] == "clean_white"
    assert not any(z.get("auto") for z in out["zooms"]) and any(s["id"] == "boom" for s in out["sfx"])   # zoom cuts off by default
    assert T.duration(out) <= T.duration(e)


def test_sfx_is_audible_in_the_render(fresh_db, tmp_path):
    from clipfarm.music import sfx
    src = tmp_path / "s.mkv"
    words = _tone_source(src, 12)
    e = _edl(src, words, 3, 9)
    ding = sfx.get("ding")
    e["sfx"] = [{"id": "ding", "path": ding["path"], "t": 6.0, "gain_db": 0}]
    res = ec.render(e, tmp_path / "o.mp4", tmp_path / "cache", "export")
    x = _audio(Path(res["path"]))
    seg = x[int(48000 * 3.0):int(48000 * 3.6)]
    spec = np.abs(np.fft.rfft(seg))
    f = np.fft.rfftfreq(seg.size, 1 / 48000)
    assert spec[(f > 1300) & (f < 1340)].max() > 0.1 * spec[f > 100].max()
