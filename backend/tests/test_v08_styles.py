"""v0.8.2 Trending Styles: every style renders with and without a facecam and with campaign overlay rules, the
split ratio / caption position / title box land where the style says, styles can be tweaked and saved, the
default comes from campaign -> account -> streamer -> automatic, and 'Make a style from this' reads a screenshot."""
from __future__ import annotations

import io

import pytest

from clipfarm import db
from clipfarm.edit import compile as ec, style_refs, styles
from clipfarm.editing import captions as C
from test_v08_bugs import _edl, _grey_source, _png

STYLES = list(styles.BUILTINS)


def _styled(src, key, facecam=True, overlay=False):
    e = _edl(src, 1.0, 7.0, "split" if facecam else "crop", "bold_pop")
    if not facecam:
        e["layout"]["facecam"] = None
        e["layout"]["keyframes"][0]["cam"] = None
    if overlay:
        e["rules"]["onscreen"] = ["streamer_overlay"]
    e["moment"]["peak"] = 4.0
    e["zooms"] = [{"t": 3.85, "kind": "punch", "amount": 0.12, "dur": 1.0}]
    return styles.apply(e, key)


@pytest.mark.parametrize("key", STYLES)
@pytest.mark.parametrize("facecam,overlay", [(True, False), (False, False), (True, True)])
def test_every_style_renders(fresh_db, tmp_path, key, facecam, overlay):
    src = tmp_path / "s.mp4"
    _grey_source(src, 9)
    e = _styled(src, key, facecam, overlay)
    s = styles.get(key)
    lays = {k["layout"] for k in e["layout"]["keyframes"]}
    if overlay:
        assert lays == {"blur_fill"}                       # the campaign's overlay rule still wins
    elif s["layout"]["mode"] == "split" and facecam:
        assert lays == {"split"} and e["layout"]["cam_h"] == styles.cam_px(s["layout"]["cam_ratio"])
    elif s["layout"]["mode"] == "full_reaction":
        assert lays == ({"focus"} if facecam else {"crop"})
    elif s["layout"]["mode"] == "corner" and facecam:
        assert lays <= {"corner", "focus"}
    elif s["layout"]["mode"] == "gameplay":
        assert lays.isdisjoint({"split", "pip", "cam_full", "corner", "focus"})
    still = ec.render_frame(e, 1.0, tmp_path / "f.png", tmp_path / "cache")
    img = _png(tmp_path / "f.png")
    assert img.shape == (1920, 1080, 3)
    # title: white box = a run of near-white pixels near the top; none = no title at all
    top = img[200:420]
    white = ((top.min(axis=2) > 235).sum(axis=1) > 300).sum()
    if s["title"]["box"] == "white_box":
        assert white >= 20, "white title box missing"
        assert still["title"]["box"] == "white_box"
    if s["title"]["box"] == "none":
        assert still["title"] is None
    # captions: one fixed place for the whole clip: the bottom, or (Split Screen, v0.8.8) on the seam
    on_seam = s["captions"]["anchor"] == "seam" and lays == {"split"}
    want = e["layout"]["cam_h"] + 40 if on_seam else C.BASELINE
    assert {ev["baseline"] for ev in still["events"]} == {want}


def test_split_ratio_and_bottom_captions(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _grey_source(src, 9)
    for ratio in (0.35, 0.40, 0.45):
        st = styles.get("streamer_split")
        st["layout"]["cam_ratio"] = ratio
        e = styles.apply(_styled(src, "streamer_split"), st)
        still = ec.render_frame(e, 2.0, tmp_path / f"r{ratio}.png", tmp_path / "cache")
        img = _png(tmp_path / f"r{ratio}.png")
        cam_h = styles.cam_px(ratio)
        # the facecam (dark blue) fills the top cam_h rows, gameplay (grey) the rest; the seam is exactly there
        col = img[:, 60]
        bluish = (col[:, 2] - col[:, 0] > 10)
        assert abs(int(bluish[:1500].sum()) - cam_h) <= 12, (ratio, int(bluish.sum()), cam_h)
        assert {ev["baseline"] for ev in still["events"]} == {C.BASELINE}       # captions never on the seam now


def test_no_highlight_style_keeps_the_text_colour(fresh_db, tmp_path):
    st = styles.get("clean")
    assert st["captions"]["highlight"] is False
    tr = C.build_track([{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate("a b c d".split())],
                       tmp_path / "c.ass", {**C.template_from("minimal").as_dict(), "highlight_on": False, "base": "minimal"},
                       2, duration=2.0, vocab=False)
    assert "FFE600" not in tr.path.read_text(encoding="utf-8").upper()


def test_style_tweaks_are_validated_and_saved(fresh_db):
    s = styles.get("streamer_split")
    s["layout"]["cam_ratio"] = 0.9
    s["captions"]["highlight_color"] = "#zz00zz"
    s["captions"]["words"] = 7
    s["captions"]["position"] = "sideways"
    saved = styles.save("My split", s)
    assert saved["key"].startswith("user:") and saved["layout"]["cam_ratio"] == 0.45
    assert saved["captions"]["highlight_color"] == "FFE600" and saved["captions"]["words"] == 6
    assert "position" not in saved["captions"]                     # captions have one fixed place
    assert any(x["key"] == saved["key"] for x in styles.all_styles())
    assert styles.last_updated()
    styles.delete(saved["id"])
    assert styles.get(saved["key"])["key"] == styles.DEFAULT


def test_default_resolution_order(fresh_db):
    assert styles.default_for(has_facecam=True) == ("streamer_corner", "automatic")
    assert styles.default_for(has_facecam=False) == ("gameplay_only", "automatic")
    assert styles.default_for(has_facecam=True, category="Just Chatting")[0] == "full_reaction"
    styles.set_default("streamer:5", "meme_high_energy")
    assert styles.default_for(streamer_id=5, has_facecam=True) == ("meme_high_energy", "streamer")
    styles.set_default("account:2", "clean")
    assert styles.default_for(streamer_id=5, account_ids=[2]) == ("clean", "account")
    styles.set_default("program:9", "gameplay_only")
    assert styles.default_for(program_id=9, streamer_id=5, account_ids=[2]) == ("gameplay_only", "campaign")
    with pytest.raises(ValueError):
        styles.set_default("planet:1", "clean")


def test_silence_cuts_keep_the_payoff_and_switch_off(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _grey_source(src, 9)
    e = _edl(src, 1.0, 8.0, "split", "bold_pop")
    ws = [{"word": "go", "start": 1.2, "end": 1.4}, {"word": "now", "start": 2.6, "end": 2.8},
          {"word": "what", "start": 6.5, "end": 6.7}]
    e["captions"]["words"] = ws
    e["moment"]["peak"] = 4.5                              # the gap 2.8-6.5 holds the payoff: never cut
    a = styles.apply(e, "streamer_split")
    assert a["style_cuts"] and all(not (c[0] < 7.0 and c[1] > 2.5) for c in a["style_cuts"])
    b = styles.apply(a, "clean")                            # Clean doesn't cut silences: the style's cuts go
    assert not b["style_cuts"] and b["cuts"] == e["cuts"]


def test_effects_follow_the_style(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _grey_source(src, 9)
    m = _styled(src, "meme_high_energy")
    kinds = {o["kind"] for o in m["overlays"]}
    assert "flash" in kinds and "shake" not in kinds                 # v0.8.5: no style shakes
    assert m["zooms"][0]["amount"] == 0.12                           # your own zoom is kept as it is
    c = styles.apply(m, "clean")
    assert not [o for o in c["overlays"] if o.get("from_style")] and c["zooms"][0]["amount"] == 0.12
    assert c["style_fx"] == {"sfx": False, "emoji": False}


# ---- make a style from a screenshot -------------------------------------------------------------------------
def _shot(tmp_path, src, key) -> bytes:
    e = _styled(src, key)
    e["hook"]["text"] = "He did NOT see this"
    ec.render_frame(e, 1.6, tmp_path / f"{key}.png", tmp_path / "cache")
    return (tmp_path / f"{key}.png").read_bytes()


def test_make_style_from_a_streamer_split_screenshot(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _grey_source(src, 9)
    res = style_refs.make_style(_shot(tmp_path, src, "streamer_split"), "Copied split")
    f, s = res["found"], res["style"]
    assert f["layout"] == "split" and abs(f["seam_y"] - 0.40) <= 0.02, f
    assert f["caption_position"] == "lower" and f["highlight_color"] == "FFE600", f
    assert f["title_box"] == "white_box", f
    assert s["layout"]["mode"] == "split" and abs(s["layout"]["cam_ratio"] - 0.40) <= 0.02
    assert s["title"]["box"] == "white_box"
    assert res["method"] == "image analysis"               # no vision model configured


def test_make_style_from_a_gameplay_screenshot(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _grey_source(src, 9)
    res = style_refs.make_style(_shot(tmp_path, src, "gameplay_only"))
    f = res["found"]
    assert f["layout"] != "split" and f["caption_position"] == "lower" and f["title_box"] in (None, "outline"), f
    assert res["style"]["layout"]["mode"] in ("gameplay", "full_reaction")


def test_vision_model_answer_is_merged(fresh_db, monkeypatch):
    found = {"layout": "gameplay", "seam_y": None, "caption_position": "lower", "words": 2, "highlight_color": None,
             "title_box": None}
    s = style_refs.style_from(found, {"energy": "meme", "uppercase": False, "title_box": "black_box",
                                      "highlight_color": "#3DFF5A"})
    assert s["base"] == "meme_high_energy" and s["captions"]["uppercase"] is False
    assert s["title"]["box"] == "black_box" and s["captions"]["highlight_color"] == "3DFF5A"


def test_reference_library_and_tags_feed_the_viral_model(fresh_db, tmp_path):
    from PIL import Image
    from clipfarm.highlights import viral_model
    buf = io.BytesIO()
    Image.new("RGB", (540, 960), (80, 80, 80)).save(buf, "PNG")
    r = style_refs.add(buf.getvalue(), "Kai-style split", ["split", "fail", "rage"], "my screenshot")
    assert r["id"] and r["tags"] == ["split", "fail", "rage"] and r["created_at"]
    assert style_refs.all_refs()[0]["id"] == r["id"]
    with pytest.raises(ValueError):
        style_refs.add(b"not an image")
    assert viral_model.trend_tags(None).get("fail") == 1.0
    style_refs.delete(r["id"])
    assert style_refs.all_refs() == []


def test_auto_edit_picks_the_style_and_caption_defaults_still_win(fresh_db, tmp_path, monkeypatch):
    from clipfarm.edit import build, templates
    src = tmp_path / "s.mp4"
    _grey_source(src, 9)
    monkeypatch.setattr(build.reframe, "analyse", lambda *a, **k: {
        "keyframes": [{"t": 1.0, "layout": "split", "cam": {"x": 0.729, "y": 0.037, "w": 0.25, "h": 0.296},
                       "path": [[0, 0.5]]}], "facecam": {"x": 0.729, "y": 0.037, "w": 0.25, "h": 0.296}, "mode": "auto"})
    sid = db.ex("INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status) "
                "VALUES ('twitch','1','x','X','allowed')")
    kw = dict(media=src, start=1.0, end=7.0, words=[], streamer={"id": sid, "display_name": "X"}, program=None,
              req={"tags": [], "onscreen": []}, settings={}, moment={"type": "Funny"}, hook="Hi", credit=None,
              account_ids=[], payoff=3.0, category="Grand Theft Auto VI")
    e = build.auto_edit(**kw)
    assert e["style"]["key"] == "streamer_corner" and e["style"]["source"] == "automatic"
    assert e["zooms"] == []                                            # v0.8.5: no automatic zoom by default
    assert e["music"]["enabled"] is False                              # v0.8.7: original stream audio only
    db.set_setting("editing", {"zoom_cuts": True})
    assert [z["t"] for z in build.auto_edit(**kw)["zooms"]] == [3.85]  # switched on: one instant cut on the payoff
    db.set_setting("editing", {})
    templates.set_default("global", "clean_white")
    e = build.auto_edit(**kw)
    assert e["captions"]["template"] == "clean_white"
    assert build.auto_edit(**{**kw, "style": "meme_high_energy"})["style"]["source"] == "clip"

