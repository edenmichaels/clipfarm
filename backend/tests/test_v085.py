"""v0.8.5: no panning ever, facecam exact crop + corner / focus layouts, static bottom captions (one renderer),
styles updated, old projects and saved styles converted (migration v12)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipfarm import db
from clipfarm.edit import compile as ec, facecam as F, project, styles
from clipfarm.editing import captions as C
from clipfarm.editing.layout import Box, refine_facecam
from test_v08_bugs import _edl, _frames, _png

CAM = {"x": 1400 / 1920, "y": 40 / 1080, "w": 480 / 1920, "h": 320 / 1080}


def _static_source(path: Path, dur: float = 10.0) -> None:
    """A completely STILL, detailed picture with a 'webcam' box: any pan, drift, zoom or shake moves its pixels."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"smptehdbars=s=1920x1080:r=30:d={dur}",
                    "-f", "lavfi", "-i", f"rgbtestsrc=s=480x320:r=30:d={dur}", "-f", "lavfi", "-i", f"sine=f=330:d={dur}",
                    "-filter_complex", "[0:v]drawgrid=w=64:h=64:t=2:c=white@0.6[g];[g][1:v]overlay=1400:40[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-c:a", "aac",
                    str(path)], check=True)


def _shift(a: np.ndarray, b: np.ndarray, r: int = 3) -> tuple[int, int]:
    """The (dx, dy) that best lines b up with a: (0, 0) means nothing moved."""
    g1, g2 = a.mean(axis=2), b.mean(axis=2)
    best = None
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            d = np.abs(g1[r:-r, r:-r] - np.roll(np.roll(g2, dy, 0), dx, 1)[r:-r, r:-r]).mean()
            if best is None or d < best[0] - 1e-9:
                best = (d, (dx, dy))
    return best[1]


def _cam_edl(src, layouts=(("corner", 1.0),), a=1.0, b=8.0):
    e = _edl(src, a, b, "corner", "bold_pop")
    e["layout"]["facecam"] = CAM
    e["layout"]["keyframes"] = [{"t": t, "layout": lay, "cam": CAM, "path": [[0, 0.5]]} for lay, t in layouts]
    e["layout"]["pip"] = {"corner": "tl", "w": 0.34}
    return e


# ---- 1. no panning, ever -------------------------------------------------------------------------------------
@pytest.mark.parametrize("key", list(styles.BUILTINS))
def test_every_style_keeps_each_shot_still(fresh_db, tmp_path, key):
    """Inside a shot, the still gameplay must not move by a single pixel (no pan, drift, zoom or shake)."""
    src = tmp_path / "s.mp4"
    _static_source(src)
    e = _cam_edl(src, (("corner", 1.0), ("crop", 4.5)))
    e["moment"]["peak"] = 3.0
    e["zooms"] = [{"t": 3.0, "kind": "punch", "amount": 0.16, "dur": 0.6, "auto": True}]
    e = styles.apply(e, key)
    assert all(len(k["path"]) == 1 for k in e["layout"]["keyframes"])        # one fixed crop per shot
    if not styles.get(key)["pacing"]["zoom_cuts"]:
        assert not [z for z in e["zooms"] if z.get("auto") or z.get("from_style")]   # zooms off by default
    assert not [o for o in e["overlays"] if o["kind"] == "shake"]
    res = ec.render(e, tmp_path / "o.mp4", tmp_path / "cache", "export")
    from clipfarm.edit import timeline as T
    segs = [(T.to_out(e, k["t"]) or 0.0) for k in e["layout"]["keyframes"]] + [T.duration(e)]
    for s0, s1 in zip(segs, segs[1:]):
        if s1 - s0 < 1.2:
            continue
        ts = np.linspace(s0 + 0.3, s1 - 0.3, 4)
        fr = _frames(res["path"], list(ts))
        band = (slice(900, 1150), slice(40, 1040))         # below any facecam, above the captions
        for f1, f2 in zip(fr, fr[1:]):
            assert _shift(f1[band], f2[band]) == (0, 0), f"{key}: the picture moved inside a shot"


def test_zoom_cuts_are_instant(fresh_db, tmp_path):
    from clipfarm.editing.render import RenderSpec, _zoom_expr
    z = _zoom_expr(RenderSpec(source=tmp_path, start=0, end=1, out=tmp_path,
                              zooms=[{"t": 2.0, "amount": 0.2, "dur": 1.0}, {"t": 4.0, "kind": "pulse", "amount": 0.1}]))
    assert "between(t,2.000,3.000)" in z and "exp(" not in z and "min(1," not in z      # a step, no ramps or pulses
    assert _zoom_expr(RenderSpec(source=tmp_path, start=0, end=1, out=tmp_path)) is None   # nothing by default


def test_auto_effects_add_no_zoom_or_shake_by_default(fresh_db, tmp_path):
    from clipfarm.edit import effects
    e = _edl(tmp_path / "x.mp4", 1, 8, "crop", "bold_pop")
    e["moment"].update(type="Action + Reaction", peak=4.0)
    a = effects.auto_effects(e, sfx=False, broll=False)
    assert not a["zooms"] and not [o for o in a["overlays"] if o["kind"] == "shake"]
    e["pacing"] = {"zoom_cuts": True}
    assert effects.auto_effects(e, sfx=False, broll=False)["zooms"]


def test_old_projects_are_converted_once_with_a_notice(fresh_db, tmp_path):
    e = _edl(tmp_path / "x.mp4", 1, 8, "crop", "bold_pop")
    e.pop("framing")
    e["layout"]["keyframes"] = [{"t": 1, "layout": "crop", "path": [[0, 0.2], [2, 0.6], [4, 0.7]]}]
    e["zooms"] = [{"t": 2, "kind": "pulse", "amount": 0.04}, {"t": 3, "kind": "punch", "amount": 1.16}]
    e["overlays"] = [{"kind": "shake", "t": 3, "dur": 0.4}]
    e["transitions"] = [{"t": 2, "kind": "whip", "dur": 0.4}]
    cid = db.ex("INSERT INTO clips(title) VALUES ('old')")
    pid = db.ex("INSERT INTO edit_projects(clip_id, data, head, status) VALUES (?,?,1,'edited')", (cid, json.dumps(e)))
    db.ex("INSERT INTO edit_versions(project_id, version, data, label) VALUES (?,1,?,'x')", (pid, json.dumps(e)))
    p = project.get(cid)
    k = p["edl"]["layout"]["keyframes"][0]
    assert k["path"] == [[0.0, 0.5]] and p["edl"]["framing"] == "fixed"
    assert [z["kind"] for z in p["edl"]["zooms"]] == ["punch"] and not p["edl"]["overlays"]
    assert p["edl"]["transitions"][0]["kind"] == "zoom"
    msg = project.notice(cid)
    assert msg.startswith("Fixed framing:") and "camera pan" in msg and "\n" not in msg
    assert p["head"] == 1 and len(project.history(cid)) == 1          # converted in place, no extra undo step
    project.notice(cid, clear=True)
    assert project.notice(cid) is None
    assert project.get(cid)["edl"] == p["edl"]


# ---- 2. facecam -------------------------------------------------------------------------------------------
def test_facecam_crop_snaps_inside_the_border():
    rng = np.random.default_rng(3)
    n, h, w = 12, 360, 640
    st = np.zeros((n, h, w), np.float32)
    for i in range(n):
        st[i] = np.roll(rng.normal(90, 40, (h // 8, w // 8)).repeat(8, 0).repeat(8, 1), i * 7, axis=1)
        st[i, 18:131, 430:621] = 230                                       # a 5 px overlay border
        st[i, 23:126, 435:616] = rng.normal(120, 35, (103, 181))          # the moving webcam picture
    for rough in (Box(400 / w, 5 / h, 240 / w, 150 / h), Box(445 / w, 30 / h, 150 / w, 90 / h)):
        b, conf = refine_facecam(st, rough)
        px = (round(b.x * w), round(b.y * h), round((b.x + b.w) * w), round((b.y + b.h) * h))
        assert px == (435, 23, 616, 126) and conf > 0.8, (px, conf)


def test_which_facecam_box_wins_and_asking_once(fresh_db):
    det, mine = {"x": 0.7, "y": 0.05, "w": 0.25, "h": 0.3}, {"x": 0.71, "y": 0.04, "w": 0.24, "h": 0.28}
    assert F.resolve(det, 0.9, None) == (det, "detected")
    assert F.resolve(det, 0.3, mine) == (mine, "saved")
    assert F.resolve(det, 0.3, None) == (det, "guess")
    assert F.resolve(None, 0.0, None) == (None, "none")
    assert F.should_ask(7, "guess") is True and F.should_ask(7, "guess") is False
    assert F.should_ask(8, "detected") is False


def test_corner_avoids_the_hud():
    imp = np.zeros((96, 54), np.float32)
    imp[10:25, 30:54] = 1.0                                   # a minimap / wanted level, top right
    assert F.choose_corner(imp, 16 / 9, False) == "tl"
    imp = np.zeros((96, 54), np.float32)
    imp[10:25, 0:24] = 1.0
    assert F.choose_corner(imp, 16 / 9, False) == "tr"


def test_pip_never_covers_title_or_captions():
    tp = C.template_from("bold_pop")
    cap = C.caption_box(2, tp)
    for corner in F.CORNERS:
        for wf in (0.25, 0.34, 0.45):
            for title in (False, True):
                x, y, w, h = F.pip_rect({"corner": corner, "w": wf}, 16 / 9, title)
                assert y + h < cap["top"] and C.SAFE["top"] <= y
                if title and corner in ("tl", "tr"):
                    assert y >= 430                       # under a 2-line title (TITLE_TOP 245, ~185 px)
                assert 0 < x and x + w < 1080 and 0.24 <= w / 1080 <= 0.46


def test_focus_plan_cuts_at_sentence_starts_and_never_too_often():
    words, t = [], 2.0
    for sent in ["so I was telling chat about this guy", "and he just walked off the bridge", "anyway look at this"]:
        for wd in sent.split():
            words.append({"word": wd, "start": round(t, 2), "end": round(t + 0.3, 2)})
            t += 0.36
        words[-1]["word"] += "."
        t += 0.6
    motion = [(x, 2.0 if x < 2 or x > 13 else 0.2) for x in np.arange(0, 20, 0.5)]
    plan = F.plan_focus(0.0, 20.0, words, motion)
    assert any(lay == "focus" for _, lay in plan)
    busy = [(x, 2.0) for x in np.arange(0, 20, 0.5)]
    assert all(lay == "corner" for _, lay in F.plan_focus(0.0, 20.0, words, busy[:20] + [(x, 2.0) for x in np.arange(10, 20, 0.5)]))
    times = [t for t, _ in plan]
    assert all(b - a >= F.SWITCH_GAP - 1e-6 for a, b in zip(times, times[1:]))
    for t, _ in plan[1:]:
        assert not any(w["start"] < t < w["end"] for w in words)          # never mid-word


def test_corner_and_focus_render(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _static_source(src)
    e = _cam_edl(src, (("corner", 1.0), ("focus", 4.5)))
    e["layout"]["pip"] = {"corner": "tr", "w": 0.34}
    a = _png(Path(ec.render_frame(e, 1.5, tmp_path / "c.png", tmp_path / "cache")["path"]))
    x, y, w, h = F.pip_rect(e["layout"]["pip"], F.cam_aspect(CAM, 1920, 1080), True)
    border = a[y + h // 2, x + 1:x + 4]                     # the thin white border on the left edge
    assert border.min() > 200, border
    inner = a[y + 20:y + h - 20, x + 20:x + w - 20]
    assert inner.std() > 20                                  # the webcam picture is in there
    f = _png(Path(ec.render_frame(e, 5.0, tmp_path / "f.png", tmp_path / "cache")["path"]))
    assert np.abs(f[900:1000, 100:900] - a[900:1000, 100:900]).mean() > 20   # the streamer fills the frame now


# ---- 3. captions -----------------------------------------------------------------------------------------
def _cap_mask(img: np.ndarray, bg: int = 0x50) -> np.ndarray:
    """Every pixel the caption touches (fill, outline, shadow) on a flat grey background -- the same whatever
    colour the spoken word is."""
    return np.abs(img - bg).max(axis=2) > 24


@pytest.mark.parametrize("layouts", [(("corner", 1.0), ("focus", 3.4), ("split", 5.8)), (("crop", 1.0),),
                                     (("blur_fill", 1.0),)])
def test_captions_have_one_baseline_and_centre_in_every_layout(fresh_db, tmp_path, layouts):
    from test_v08_bugs import _grey_source
    src = tmp_path / "s.mp4"
    _grey_source(src, 10)
    e = _cam_edl(src, layouts)
    e["hook"]["text"] = None
    tr = C.build_track(e["captions"]["words"], tmp_path / "c.ass", "bold_pop", offset=e["range"][0], duration=7.0)
    assert {ev["baseline"] for ev in tr.events} == {C.BASELINE}
    assert {ev["x"] for ev in tr.events} == {C.CAP_X}
    bottoms, centres = [], []
    for t in np.arange(0.6, 6.6, 0.45):
        img = _png(Path(ec.render_frame(e, float(t), tmp_path / f"f{t:.2f}.png", tmp_path / "cache")["path"]))
        m = (_cap_mask(img) & (np.abs(img - 0x30).max(axis=2) > 24))[1150:1560]    # not grey, not the cam
        ys, xs = np.nonzero(m)
        if len(ys) < 200:
            continue
        rows = np.nonzero(m.sum(axis=1) > 20)[0]
        bottoms.append(1150 + rows.max())
        centres.append((xs.min() + xs.max()) / 2)
        # never over the facecam corner or the title
        if v := F.pip_rect(e["layout"]["pip"], F.cam_aspect(CAM, 1920, 1080), False):
            assert 1150 + rows.min() > v[1] + v[3]
    assert len(bottoms) >= 8
    assert max(bottoms) - min(bottoms) <= 0.005 * 1920, bottoms       # same baseline (±0.5 % of the height)
    assert max(centres) - min(centres) <= 0.01 * 1080, centres         # same centre (±0.5 % each side)


def test_glyphs_never_change_size_or_position_within_a_caption(fresh_db, tmp_path):
    """Frame by frame through one caption while the highlight moves word to word.
    With the highlight colour set to white, every frame is pixel-identical (switching events moves nothing);
    with the yellow highlight, the only pixels that differ are inside the lit word."""
    from clipfarm.editing.ffmpeg_util import ffmpeg_bin
    words = [{"word": w, "start": 0.2 + i * 0.4, "end": 0.5 + i * 0.4} for i, w in enumerate(["NO", "WAY", "BRO"])]

    def frames(style):
        tr = C.build_track(words, tmp_path / "c.ass", style, vocab=False)
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x505050:s=1080x1920:r=30:d=1.6",
                        "-vf", f"format=rgb24,ass=c.ass:fontsdir={C.fonts_dir()}", "-pix_fmt", "rgb24",
                        "-f", "rawvideo", "out.raw"], check=True, cwd=tmp_path)
        return tr, np.fromfile(tmp_path / "out.raw", np.uint8).reshape(-1, 1920, 1080, 3).astype(int)[8:40]
    _, plain = frames({"base": "bold_pop", "highlight": "FFFFFF"})
    for a, b in zip(plain, plain[1:]):
        assert np.array_equal(a, b), "a glyph moved or changed size when the caption advanced a word"
    tr, lit = frames("bold_pop")
    for f, p in zip(lit, plain):
        ys, xs = np.nonzero(np.abs(f - p).max(axis=2) > 0)
        if len(ys):                                     # the lit word only: one compact area on one line
            assert ys.max() - ys.min() < 0.75 * 124 * 1.5 and xs.max() - xs.min() < 0.6 * 1080
    assert len({tuple(e["hi"][0]) + tuple(e["hi"][-1]) for e in tr.events}) == 3      # the highlight did move


def test_no_animation_tags_and_one_caption_renderer():
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate("this is a test of it".split())]
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        txt = C.build_track(words, Path(d) / "c.ass", "bold_pop", hook="Big title", vocab=False).path.read_text("utf-8")
    for tag in ("\\t(", "\\fad", "\\fscx", "\\fscy", "\\move", "\\blur", "\\k", "\\alpha&HFF"):
        assert tag not in txt, tag
    src = Path(C.__file__).read_text(encoding="utf-8")
    assert "ANIMATIONS" not in src and "bounce" not in src and "typewriter" not in src
    api_src = (Path(C.__file__).parents[1] / "api.py").read_text(encoding="utf-8")
    assert "/canvas" not in api_src and "caption-preview" not in api_src     # the old preview-only paths are gone


def test_caption_timing_rules():
    pr = C.template_from("bold_pop")
    ws = [{"word": "hey", "start": 0.0, "end": 0.2}, {"word": "you.", "start": 0.25, "end": 0.45},
          {"word": "look", "start": 0.5, "end": 0.7}, {"word": "at", "start": 0.72, "end": 0.8},
          {"word": "this", "start": 0.82, "end": 1.0}, {"word": "WOW", "start": 3.0, "end": 3.1}]
    caps = C.captions_from(ws, pr, 3, True, duration=5.0)
    assert [" ".join(w["word"] for w in c["words"]) for c in caps] == ["hey you.", "look at this", "WOW"]
    for a, b in zip(caps, caps[1:]):
        assert a["end"] <= b["start"] + 1e-9                                       # never overlap
    assert caps[0]["end"] == caps[1]["start"]                                       # no gap while talking
    assert caps[1]["end"] <= 1.0 + C.HOLD + 1e-9 < 3.0                              # nothing in the silence
    assert all(c["end"] - c["start"] >= C.MIN_CAPTION - 1e-9 for c in caps)         # never shorter than 0.3 s


def test_long_phrases_split_instead_of_shrinking():
    pr = C.template_from("bold_pop")
    ws = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
          for i, w in enumerate("he was going crazy fast down the big hill and then unbelievable".split())]
    caps = C.captions_from(ws, pr, 6, True)
    assert len(caps) >= 2 and all(len(c["lines"]) <= 2 for c in caps)
    for c in caps:
        if c.get("condense"):          # one word too wide on its own: narrowed (never smaller), alone on screen
            assert len(c["words"]) == 1 and 0.6 < c["condense"] < 1
            continue
        for line in c["lines"]:
            assert C.drawn_width(" ".join(w["word"].upper() for w in line), pr) <= C.CAP_WIDTH
    assert any(c.get("condense") for c in caps)                    # UNBELIEVABLE


def test_captions_stay_in_the_safe_zone_and_off_the_title():
    tp = C.template_from("bold_pop")
    for n in (1, 2):
        b = C.caption_box(n, tp)
        assert C.SAFE["left"] <= b["x0"] and b["x1"] <= C.SAFE["right"]
        assert b["bottom"] <= C.SAFE["bottom"] and 0.75 * 1920 <= b["baseline"] <= 0.80 * 1920
        assert b["top"] > 245 + 200                                       # far below the title


def test_preview_and_export_draw_the_same_captions(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _static_source(src)
    e = _cam_edl(src)
    still = ec.render_frame(e, 2.0, tmp_path / "p.png", tmp_path / "cache")
    res = ec.render(e, tmp_path / "o.mp4", tmp_path / "cache", "export")
    a, b = _png(Path(still["path"])), _frames(res["path"], [2.0])[0]
    white = lambda x: (x.min(axis=2) > 200)[1200:1560]                       # noqa: E731
    ma, mb = white(a), white(b)
    assert ma.sum() > 2000 and (ma != mb).mean() < 0.01


def test_vocabulary_fixes_names_and_terms(fresh_db):
    from clipfarm.editing import vocab
    sid = db.ex("INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status) "
                "VALUES ('twitch','9','kreek','KreekCraft','allowed')")
    assert sid
    v = vocab.get()
    assert "KreekCraft" in v["terms"] and "Vice City" in v["terms"]
    vocab.set_(v["terms"], [{"from": "vice sitty", "to": "Vice City"}])
    ws = [{"word": w, "start": i, "end": i + 0.5} for i, w in enumerate("kreekkraft in vice sitty leonida!".split())]
    assert [w["word"] for w in vocab.fix_words(ws)] == ["KreekCraft", "in", "Vice City", "Leonida!"]
    assert "KreekCraft" in vocab.prompt()


def test_font_is_montserrat_and_licensed():
    assert C.template_from(None).family == "Montserrat Black" and C.template_from(None).uppercase
    lic = Path(C.fonts_dir()) / "OFL-Montserrat.txt"
    assert lic.exists() and "SIL OPEN FONT LICENSE" in lic.read_text(encoding="utf-8").upper()
    for p in C.PRESETS.values():
        assert p.family.startswith("Montserrat") and 80 <= p.em * 0.7 <= 95       # cap height 80-95 px


# ---- 4. styles + migration v12 -------------------------------------------------------------------------
def test_streamer_corner_is_the_default_with_a_facecam(fresh_db):
    assert styles.default_for(has_facecam=True) == ("streamer_corner", "automatic")
    for s in styles.BUILTINS.values():
        assert s["pacing"]["zoom_cuts"] is False and "shake" not in s["effects"]
        assert "position" not in s["captions"] and "position" not in s["title"]


def test_migration_v12_converts_saved_styles_and_projects(fresh_db):
    from clipfarm import migrations
    old_style = {"name": "My seam", "layout": {"mode": "split"}, "captions": {"position": "seam", "words": 2},
                 "title": {"position": "seam"}, "effects": {"shake": True}, "pacing": {"zoom": 0.8}}
    db.ex("INSERT INTO styles(name, data) VALUES ('My seam', ?)", (json.dumps(old_style),))
    db.ex("INSERT INTO styles(name, data) VALUES ('Fine', ?)", (json.dumps({"name": "Fine"}),))
    e = _edl(Path("x.mp4"), 1, 8, "crop", "bold_pop")
    e.pop("framing")
    e["layout"]["keyframes"] = [{"t": 1, "layout": "crop", "path": [[0, 0.3], [3, 0.7]]}]
    cid = db.ex("INSERT INTO clips(title) VALUES ('old')")
    pid = db.ex("INSERT INTO edit_projects(clip_id, data, head, status) VALUES (?,?,1,'edited')", (cid, json.dumps(e)))
    db.ex("INSERT INTO edit_versions(project_id, version, data, label) VALUES (?,1,?,'x')", (pid, json.dumps(e)))
    db.set_setting("facecam:5", {"x": 0.7, "y": 0, "w": 0.3, "h": 0.3})
    conn = db.connect()
    migrations._v12(conn)
    conn.commit()
    notes = db.get_setting("style_notices")
    assert [n["name"] for n in notes] == ["My seam"]
    assert any("static at the bottom" in c for c in notes[0]["changes"]) and any("shake" in c for c in notes[0]["changes"])
    data = json.loads(db.q1("SELECT data FROM styles WHERE name='My seam'")["data"])
    assert "position" not in data["captions"] and "shake" not in data["effects"] and data["pacing"]["zoom_cuts"] is False
    stored = json.loads(db.q1("SELECT data FROM edit_projects WHERE id=?", (pid,))["data"])
    assert stored["framing"] == "fixed" and stored["layout"]["keyframes"][0]["path"] == [[0.0, 0.5]]
    assert "camera pan" in project.notice(cid)
    assert db.get_setting("facecam:5")["by"] == "auto"
