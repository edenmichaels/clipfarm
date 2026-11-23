"""v0.8 step 1: no guide/debug pixels in exports, captions that stay put, preview == export, and the moment
finder no longer rewards sad talk, condolence chat or calm driving."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipfarm.edit import compile as ec, project
from clipfarm.highlights import detect, signals

LAYOUTS = ["split", "crop", "blur_fill", "speaker", "pip"]
TEMPLATES = ["bold_pop", "hype_yellow", "clean_white", "minimal"]


def _grey_source(path: Path, dur: float = 12.0) -> None:
    """Flat grey 'gameplay' with a darker 'facecam' box top-right and a tone, so any red/guide pixel stands out."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x505050:s=1920x1080:r=30:d={dur}",
                    "-f", "lavfi", "-i", f"color=c=0x303848:s=480x320:r=30:d={dur}",
                    "-f", "lavfi", "-i", f"sine=f=330:d={dur}",
                    "-filter_complex", "[0:v][1:v]overlay=1400:40[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)], check=True)


def _words(a: float, b: float) -> list[dict]:
    toks = "no way bro he actually did it what a legend that was insane".split()
    out, t, i = [], a, 0
    while t < b - 0.4:
        out.append({"word": toks[i % len(toks)], "start": round(t, 2), "end": round(t + 0.32, 2)})
        t += 0.42
        i += 1
    return out


def _edl(src: Path, a: float, b: float, layout: str, template: str) -> dict:
    e = project.blank({"path": str(src), "url": None, "start": 0, "end": 30, "width": 1920, "height": 1080, "fps": 30,
                       "has_audio": True}, _words(a + 0.3, b), a, b)
    e["layout"]["facecam"] = {"x": 0.729, "y": 0.037, "w": 0.25, "h": 0.296}
    e["layout"]["keyframes"] = [{"t": a, "layout": layout, "path": [[0, 0.5]]}]
    e["captions"]["template"] = template
    e["hook"]["text"] = "He did NOT see this"
    e["music"]["enabled"] = False
    return e


def _frames(path: str, times: list[float]) -> list[np.ndarray]:
    out = []
    for t in times:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        out.append(np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int))
    return out


def _png(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)


# ---- A. no guide/debug pixels in any export ---------------------------------------------------------------
@pytest.mark.parametrize("layout", LAYOUTS)
def test_no_guide_or_debug_pixels_in_exports(fresh_db, tmp_path, layout):
    src = tmp_path / "s.mp4"
    _grey_source(src, 6)
    for tmpl in TEMPLATES:
        e = _edl(src, 1.0, 4.0, layout, tmpl)
        res = ec.render(e, tmp_path / f"{layout}_{tmpl}.mp4", tmp_path / "cache", "export")
        for f in _frames(res["path"], [0.5, 1.5, 2.5]):
            r, g, b = f[..., 0], f[..., 1], f[..., 2]
            red = (r > 180) & (g < 90) & (b < 90)               # guide/selection/facecam-box red
            assert red.sum() < 50, f"{layout}/{tmpl}: {int(red.sum())} red guide pixels"
            # a guide is a thin straight line: no full-width or full-height single-colour line other than the
            # progress bar at the very bottom
            body = f[:1900]
            for axis in (0, 1):
                lines = (np.abs(np.diff(body, axis=axis)).sum(axis=2) > 120).mean(axis=1 - axis)
                assert lines.max() < 0.97, f"{layout}/{tmpl}: a straight guide line was drawn"


def test_preview_guides_are_page_only():
    """Safe-zone guides live in the Editor page (CSS), are off by default and aren't red."""
    root = Path(__file__).resolve().parents[2] / "app" / "src"
    parts = (root / "components" / "EditorParts.tsx").read_text(encoding="utf-8")
    editor = (root / "pages" / "Editor.tsx").read_text(encoding="utf-8")
    css = (root / "styles.css").read_text(encoding="utf-8")
    assert 'readPref("ed.zone2", "")' in editor                       # off unless you turn it on
    assert "ed-guide" in parts and "ed-safe" not in parts
    guide_css = css[css.index(".ed-guide-box"):css.index("\n", css.index(".ed-guide-box"))]
    assert "#ff" not in guide_css.lower() and "red" not in guide_css.lower()


# ---- B. captions stay put ----------------------------------------------------------------------------
def test_caption_centre_is_fixed_through_reframe_and_zoom(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _grey_source(src, 32)
    e = _edl(src, 1.0, 31.0, "split", "bold_pop")
    # the AI reframe switching layouts every few seconds, plus punch-in zooms
    e["layout"]["keyframes"] = [{"t": t, "layout": lay, "path": [[0, 0.5]]} for t, lay in
                                [(1.0, "split"), (6.0, "crop"), (11.0, "speaker"), (16.0, "blur_fill"), (21.0, "split"),
                                 (26.0, "pip")]]
    e["zooms"] = [{"t": t, "kind": "punch", "amount": 1.22, "dur": 0.8} for t in (4.0, 9.5, 14.0, 19.0, 24.0, 29.0)]
    res = ec.render(e, tmp_path / "o.mp4", tmp_path / "cache", "export")
    ev = json.loads(Path(res["path"]).with_suffix(".events.json").read_text())
    ys = {x["y"] for x in ev}
    assert len(ev) > 40 and len(ys) == 1, f"caption centre moved: {sorted(ys)}"
    set_y = ys.pop()
    # measured on the rendered frames: the caption's bright pixels stay centred within ±1% of the frame
    times = [x["start"] + 0.2 for x in ev[::6]]
    for t, f in zip(times, _frames(res["path"], times)):
        bright = f.max(axis=2)
        band = bright[int(set_y) - 200:int(set_y) + 200]
        ys_, xs_ = np.nonzero(band > 215)                      # caption text, white or highlighted (bg is grey)
        assert xs_.size > 200, f"no caption at {t:.1f}s"
        cx, cy = xs_.mean(), ys_.mean() + int(set_y) - 200
        assert abs(cx - 540) <= 0.01 * 1080 * 3, f"x {cx:.0f} at {t:.1f}s"    # words differ; centre stays put
        assert abs(cy - set_y) <= 0.01 * 1920 * 2, f"y {cy:.0f} vs {set_y} at {t:.1f}s"


def test_pop_scales_only_the_active_word_around_its_own_centre(fresh_db, tmp_path):
    from clipfarm.editing.captions import build_track
    tr = build_track(_words(0.0, 3.0), tmp_path / "c.ass", "bold_pop", 3, layout="crop", duration=3.0)
    ass = tr.path.read_text(encoding="utf-8")
    base = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: 0,") and ",Cap," in ln]
    pops = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: 1,") and ",Cap," in ln]
    assert base and pops
    assert all("\\fscx" not in ln for ln in base)                     # the line itself never scales
    assert all("\\fscx" in ln and "\\an5\\pos(" in ln for ln in pops)  # the word scales around its own centre
    # one fixed centre: every chunk's rows (1 or 2 lines, any font metrics) are centred on the same y
    rows = {}
    for ln in base:
        start = ln.split(",")[1]
        rows.setdefault(start, set()).add(int(ln.split("\\pos(")[1].split(",")[1].split(")")[0]))
    centres = [sum(v) / len(v) for v in rows.values()]
    assert max(centres) - min(centres) <= 3, sorted(centres)


def test_preview_frame_matches_export_frame(fresh_db, tmp_path):
    src = tmp_path / "s.mp4"
    _grey_source(src, 12)
    e = _edl(src, 1.0, 10.0, "split", "hype_yellow")
    e["zooms"] = [{"t": 4.0, "kind": "punch", "amount": 1.2, "dur": 0.6}]
    res = ec.render(e, tmp_path / "o.mp4", tmp_path / "cache", "export")
    for t in (1.2, 3.1, 6.4):
        still = ec.render_frame(e, t, tmp_path / f"f{t}.png", tmp_path / "cache")
        a = _png(Path(still["path"]))
        b = _frames(res["path"], [t])[0]
        assert np.abs(a - b).mean() < 3.0, f"preview and export differ at {t}s"


# ---- C. moment finder: sad talk, condolence chat and calm driving aren't highlights -------------------------
def _signals(n=300):
    ev = {k: np.full(n, 0.02, np.float32) for k in ("laughter", "scream", "cheer", "explosion", "gunshot", "crash",
                                                    "vehicle", "siren", "driving", "sad", "speech", "music", "silence")}
    ev["speech"][:] = 0.6
    msgs = [(t * 0.8, "hi") for t in range(int(n / 0.8))]
    ev["laughter"][50:58] = 0.8
    msgs += [(52 + i * 0.2, "KEKW LUL") for i in range(40)]                     # funny
    ev["sad"][90:120] = 0.35
    ev["scream"][95:100] = 0.3
    msgs += [(92 + i * 0.15, "F o7 Sadge") for i in range(150)]                 # sad story, chat sends condolences
    ev["driving"][140:200] = 0.7
    ev["speech"][140:200] = 0.1                                                  # calm driving
    ev["explosion"][215:218] = 0.9
    ev["laughter"][217:224] = 0.7
    msgs += [(218 + i * 0.2, "LMAO no way") for i in range(30)]                 # explosion + laughter
    return ev, signals.chat_reactions(msgs, n)


def test_sad_talk_and_calm_driving_are_not_picked():
    ev, ch = _signals()
    loud = np.full(300, -20.0, np.float32)
    comp, parts = detect.components(detect.Signals(300, events=ev, loudness=loud, chat=ch))
    cv = detect.curve(comp, parts)
    assert cv[80:130].max() < 0.2, "sad talk scored as a highlight"
    assert cv[140:200].max() < 0.2, "calm driving scored as a highlight"
    assert cv[40:80].max() > 0.6 and cv[210:240].max() > 0.6
    picks = detect.pick(cv, parts, comp, loud, budget=5, min_len=8, max_len=40, threshold=0.42)
    for m in picks:
        assert not (80 <= m.start <= 130 or 140 <= m.start <= 200), f"picked {m.start}-{m.end}"


def test_condolence_chat_is_not_hype():
    ch = signals.chat_reactions([(1 + i * 0.1, "F") for i in range(50)] + [(1 + i * 0.1, "o7 Sadge") for i in range(50)], 20)
    assert ch["sad"].sum() >= 90 and ch["fail"].sum() == 0 and ch["hype"].sum() == 0


def test_llm_rating_rewards_entertainment_not_emotion():
    from clipfarm.highlights import rate
    assert "serious" in rate.SYSTEM and "stop_scroll" not in rate.SYSTEM
    m = detect.Moment(start=0, end=20, peak=10, score=0.6, signals={}, type="Funny", reason="")
    sad = detect.rescore_with_llm(detect.Moment(**{**m.__dict__}), {"funny": 1, "chaos": 1, "shock": 3, "serious": 9})
    funny = detect.rescore_with_llm(detect.Moment(**{**m.__dict__}), {"funny": 9, "chaos": 6, "shock": 4, "serious": 0})
    assert funny.score > 0.6 > sad.score and sad.score < 0.35


def test_street_crowd_and_driving_sounds_are_not_reactions_or_action():
    from clipfarm.highlights.audio_events import GROUPS
    assert "Crowd" not in GROUPS["cheer"]
    assert not {"Car passing by", "Accelerating, revving, vroom", "Revving"} & set(GROUPS["vehicle"])
    assert not {"Burst, pop", "Bang", "Thump, thud"} & set(GROUPS["explosion"] + GROUPS["crash"])
    assert "Crying, sobbing" in GROUPS["sad"]
