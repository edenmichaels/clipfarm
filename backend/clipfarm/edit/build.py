"""Auto-edit: build a clip's edit project from a detected moment.

Highlight detection and auto-pilot call this. The project can be exported straight away (hands-off) or
opened in the Editor. Old clips (made before edit projects) get a project on first open.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import db
from ..editing.ffmpeg_util import probe
from ..editing.layout import Box
from ..music import pick as music_pick
from . import effects as FX, project, reframe, styles, templates
from . import timeline as T


def source_info(path: Path) -> dict:
    i = probe(path)
    return {"path": str(path), "width": i["width"], "height": i["height"], "fps": i.get("fps", 30),
            "has_audio": i["has_audio"], "start": 0.0, "end": round(i["duration"], 3)}


def auto_edit(*, media: Path, start: float, end: float, words: list[dict], streamer: dict, program: dict | None,
              req: dict, settings: dict, moment: dict, hook: str | None, credit: str | None,
              account_ids: list[int], payoff: float | None, url: str | None = None,
              category: str | None = None, style: str | None = None) -> dict:
    src = source_info(media)
    src["url"] = url
    e = project.blank(src, words, max(0.0, start), min(end, src["end"]))
    # rules (enforced in render + Editor)
    e["rules"] = {"program_id": program["id"] if program else None, "program": program["name"] if program else None,
                  "tags": req.get("tags", []), "credit": credit, "disclosure": req.get("disclosure"),
                  "onscreen": list(req.get("onscreen", [])), "no_music": bool(program and program.get("no_music")),
                  "platforms": json.loads(program["allowed_platforms"] or "[]") if program and program.get("allowed_platforms") else []}
    # reframe: one fixed crop per shot; with a facecam: gameplay + facecam in a corner, focus on the streamer
    # when they're the moment (v0.8.5)
    from . import facecam as F
    saved = F.saved_box(streamer.get("id"))
    if saved and saved.get("by") != "you":
        saved = None                       # an old automatic guess: detection (or you) decides now
    speech = [(w["start"], w["end"]) for w in words]
    lay = reframe.analyse(media, e["range"][0], e["range"][1],
                          facecam=Box(**{k: saved[k] for k in ("x", "y", "w", "h")}) if saved else None,
                          onscreen=e["rules"]["onscreen"], speech=speech, detect_cam=saved is None, words=words,
                          has_title=bool(hook), pip=db.get_setting(f"pip:{streamer.get('id')}") if streamer.get("id") else None)
    forced = settings.get("layout") or db.get_setting("layout", "auto")
    if forced and forced != "auto":
        for k in lay["keyframes"]:
            if not (k["layout"] == "blur_fill" and "streamer_overlay" in e["rules"]["onscreen"]):
                needs_cam = forced in ("split", "corner", "focus", "pip")
                k["layout"] = forced if (not needs_cam or lay["facecam"]) else k["layout"]
    lay["keyframes"], _fixed = reframe.enforce_rules(lay["keyframes"], e["rules"]["onscreen"], lay["facecam"])
    e["layout"].update(lay)
    if F.should_ask(streamer.get("id"), lay.get("facecam_source", "none")):
        e["layout"]["facecam_ask"] = True      # the Editor asks you once to draw this streamer's facecam
    # captions
    e["captions"]["template"] = templates.default_for(program["id"] if program else None, account_ids)
    # hook + moment
    e["hook"]["text"] = hook
    peak = (e["range"][0] + payoff) if payoff is not None else None
    e["moment"] = {"type": moment.get("type") or "Funny", "reason": moment.get("reason"), "peak": peak,
                   "score": (moment["viral"] / 100) if moment.get("viral") is not None else moment.get("score"),
                   "viral": moment.get("viral"), "tags": moment.get("tags") or [], "why": moment.get("why") or [],
                   "setup_start": moment.get("setup_start")}
    # zooms: OFF by default (v0.8.5). With zoom cuts on, an instant cut to a centred zoom on the payoff and back.
    if peak is not None and settings.get("zoom", True) and FX.zoom_cuts_on(e):
        e["zooms"] = [{"t": round(peak - 0.15, 3), "kind": "punch", "amount": 0.12, "dur": 1.0, "auto": True}]
    e["progress_bar"] = settings.get("progress_bar", True)
    # music: OFF (v0.8.7). Exports use the original stream audio; music is a manual Editor option only.
    e["music"].update({"enabled": False, "path": None, "track_id": None, "mode": None, "manual": False})
    # Trending Style: clip's own -> campaign -> account -> streamer -> global -> automatic
    if style or (db.get_setting("styles", {}) or {}).get("auto", True):
        key, why = (style, "clip") if style else styles.default_for(
            program_id=program["id"] if program else None, account_ids=account_ids, streamer_id=streamer.get("id"),
            has_facecam=bool(lay["facecam"]), category=category)
        tmpl = e["captions"]["template"]
        e = styles.apply(e, key)
        e["style"]["source"] = why
        d = templates.defaults()
        if (program and d.get(f"program:{program['id']}")) or any(d.get(f"account:{a}") for a in account_ids) \
                or d.get("global"):
            e["captions"]["template"] = tmpl          # an explicit caption-template default still wins
    return e


def plan_music(e: dict, account_ids: list[int], settings: dict, program: dict | None, seed=None,
               track: dict | None = None) -> dict:
    allowed, why = music_pick.music_allowed(program, settings)
    m = e["music"]
    if not allowed or e["rules"].get("no_music"):
        m.update({"enabled": False, "path": None, "track_id": None, "mode": None, "why_off": why or "Campaign rule"})
        return e
    dur = T.duration(e)
    try:
        track = track or music_pick.choose(e["moment"].get("type") or "Funny", account_ids, seed=seed)
    except Exception:
        track = None
    if not track:
        m.update({"enabled": False, "path": None, "track_id": None})
        return e
    peak = e["moment"].get("peak")
    payoff = T.to_out(e, peak) if peak is not None else dur * 0.6
    plan = music_pick.plan(track, e["moment"].get("type") or "Funny", payoff or dur * 0.6, dur)
    m.update({"enabled": True, "track_id": track["id"], "path": track["path"], "title": track["title"],
              "licence": track["licence"], "offset": plan["offset"], "entry": plan["entry"], "delay": plan["delay"],
              "beats": plan["beats"], "zoom_beats": plan["zoom_beats"], "mode": plan["mode"],
              "loudness": track.get("loudness"), "duck_db": float((db.get_setting("music", {}) or {}).get("duck_db", 15))})
    return e


def ensure_project(clip_id: int) -> dict | None:
    """Edit project for any clip: existing, or made now from what the clip kept."""
    p = project.get(clip_id)
    if p:
        return p
    c = db.q1("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not c:
        return None
    spec = json.loads(c.get("render_spec") or "{}")
    src_path = c.get("source_path") or spec.get("base")
    if not src_path or not Path(src_path).exists():
        return None
    src = source_info(Path(src_path))
    words = json.loads(c.get("words") or "[]")
    if spec.get("base") and src_path == spec.get("base"):
        # v0.4 clip: its base is already 9:16 and starts at the clip start; keep it as it is
        off = float(spec.get("start", 0))
        words = [{**w, "start": float(w["start"]) - off, "end": float(w["end"]) - off} for w in words]
    e = project.blank(src, words, 0.0, src["end"])
    e["layout"]["keyframes"] = [{"t": 0.0, "layout": "blur_fill", "scene": "imported", "cam": None, "path": [[0, 0.5]]}]
    e["captions"]["template"] = c.get("caption_preset") or "bold_pop"
    e["captions"]["nudge"] = c.get("caption_nudge") or 0
    e["hook"]["text"] = c.get("hook_text")
    e["moment"] = {"type": c.get("moment_type"), "reason": c.get("moment_reason"), "peak": None, "score": None}
    if spec.get("music") and Path(spec["music"]).exists():
        e["music"].update({"enabled": True, "path": spec["music"], "offset": spec.get("music_offset", 0),
                           "entry": spec.get("music_entry", 0), "delay": spec.get("music_delay", 0),
                           "loudness": spec.get("music_track_lufs"), "track_id": c.get("music_track_id")})
    else:
        e["music"]["enabled"] = False
    # the campaign's rules (hashtags, disclosure, platforms, original audio only) come from the clip's program
    prog = db.q1("SELECT * FROM programs WHERE id=?", (c["program_id"],)) if c.get("program_id") else None
    if prog:
        from ..campaigns import rules as camp_rules
        req = camp_rules.requirements(prog)
        e["rules"].update({"program_id": prog["id"], "program": prog["name"], "tags": req["tags"],
                           "disclosure": req["disclosure"], "no_music": bool(prog.get("no_music")),
                           "platforms": json.loads(prog["allowed_platforms"] or "[]") if prog.get("allowed_platforms") else []})
        if not spec.get("keep_clear"):
            e["rules"]["onscreen"] = list(req["onscreen"])
    e["rules"]["credit"] = spec.get("credit") or e["rules"].get("credit")
    if spec.get("keep_clear"):
        e["rules"]["onscreen"] = list(spec["keep_clear"])
    if e["rules"].get("no_music"):
        e["music"]["enabled"] = False
    return project.create(clip_id, e, "Imported from an older clip")
