"""Local HTTP API (FastAPI) consumed by the desktop UI.

Bound to 127.0.0.1 only. Every request must carry the per-launch token the Tauri shell
generates (header X-ClipFarm-Token, or ?token= for <img>/<video> URLs) so other local
programs can't drive the app.
"""
from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import os
import re
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__, compliance, db, secrets, vault
from . import health as healthmod
from .campaigns import autofill, autopilot, forecast, grouping, importer, matching
from .discovery import service as disc
from .posting import submissions as subs
from .redact import redact
from .editing.ffmpeg_util import detect_encoders
from .jobs import engine as eng
from .jobs import pipeline, storage
from .posting import platforms as posting
from .providers import available_providers

log = logging.getLogger("clipfarm.api")

TOKEN = os.environ.get("CLIPFARM_TOKEN", "")
app = FastAPI(title="ClipFarm", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
                                                  "http://localhost:1420"], allow_methods=["*"],
                   allow_headers=["*"])

FALLBACK_GAMES = ["Grand Theft Auto VI", "Grand Theft Auto V", "Fortnite", "Valorant", "Counter-Strike 2",
                  "Minecraft", "League of Legends", "Call of Duty: Warzone", "Apex Legends", "Rust"]


def auth(request: Request):
    if not TOKEN:
        return  # dev mode
    if request.headers.get("X-ClipFarm-Token") == TOKEN or request.query_params.get("token") == TOKEN:
        return
    raise HTTPException(401, "bad token")


A = [Depends(auth)]


# ---- system / settings --------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/system", dependencies=A)
def system():
    return {"encoders": detect_encoders(), "disk": storage.disk_status(),
            "connected": list(available_providers().keys()),
            "secrets": {k: secrets.has_secret(k) for k in SECRET_NAMES},
            "keychain": secrets.keychain_backend if secrets.keyring else None,
            "keychain_error": secrets.keychain_error, "vault": vault.status(), "version": __version__}


@app.get("/settings", dependencies=A)
def get_settings():
    return db.all_settings()


@app.put("/settings", dependencies=A)
def put_settings(body: dict):
    for k, v in body.items():
        db.set_setting(k, v)
    return db.all_settings()


SECRET_NAMES = secrets.SECRET_NAMES


class SecretIn(BaseModel):
    name: str
    value: str


@app.post("/secrets", dependencies=A)
def set_secret(s: SecretIn):
    if s.name not in SECRET_NAMES:
        raise HTTPException(400, "unknown secret")
    if s.value:
        secrets.set_secret(s.name, s.value.strip())
    else:
        secrets.delete_secret(s.name)
    return {"ok": True}


# ---- games & discovery -----------------------------------------------------------
@app.get("/games/trending", dependencies=A)
def trending():
    tw = available_providers().get("twitch")
    if tw:
        return [g.__dict__ for g in tw.top_games(40)]
    return [{"platform": "manual", "platform_game_id": None, "name": n, "box_art_url": None} for n in FALLBACK_GAMES]


@app.get("/games/search", dependencies=A)
def search_games(q: str):
    out = []
    for name, p in available_providers().items():
        if hasattr(p, "search_games"):
            try:
                out += [g.__dict__ for g in p.search_games(q)]
            except Exception as e:
                db.log("warn", "games", f"{name} search failed: {e}")
    if not out:
        out = [{"platform": "manual", "platform_game_id": None, "name": q, "box_art_url": None}]
    return out


class GameIn(BaseModel):
    name: str
    twitch_id: str | None = None
    kick_slug: str | None = None
    box_art_url: str | None = None


@app.post("/games", dependencies=A)
def select_game(g: GameIn):
    return {"id": disc.upsert_game(g.name, g.twitch_id, g.box_art_url, g.kick_slug)}


@app.post("/games/{game_id}/discover", dependencies=A)
def discover(game_id: int):
    errors = disc.discover(game_id)
    return {"errors": errors, "streamers": disc.ranked_streamers(game_id)}


@app.get("/games/{game_id}/streamers", dependencies=A)
def streamers(game_id: int):
    return disc.ranked_streamers(game_id)


class StreamerPatch(BaseModel):
    permission_status: str | None = None
    permission_note: str | None = None
    best_platforms: list[str] | None = None
    competition_count: int | None = None
    game_id: int | None = None


@app.patch("/streamers/{sid}", dependencies=A)
def patch_streamer(sid: int, p: StreamerPatch):
    if p.permission_status:
        if p.permission_status not in ("allowed", "program_member", "unknown", "prohibited"):
            raise HTTPException(400, "bad permission_status")
        db.ex("UPDATE streamers SET permission_status=?, permission_note=COALESCE(?, permission_note) WHERE id=?",
              (p.permission_status, p.permission_note, sid))
    if p.best_platforms is not None:
        db.ex("UPDATE streamers SET best_platforms=? WHERE id=?", (json.dumps(p.best_platforms), sid))
    if p.competition_count is not None and p.game_id:
        db.ex("UPDATE rankings SET competition_count=? WHERE streamer_id=? AND game_id=?",
              (p.competition_count, sid, p.game_id))
    if p.game_id:
        disc.rank_game(p.game_id)
    return db.q1("SELECT * FROM streamers WHERE id=?", (sid,))


# ---- rewards programs (user-editable) -----------------------------------------------
class ProgramIn(BaseModel):
    name: str
    operator: str = "other"
    url: str | None = None
    payout_per_1k: float | None = None
    currency: str = "USD"
    min_views: int | None = None
    max_payout: float | None = None
    allowed_platforms: list[str] = []
    required_tags: list[str] = []
    required_credit: str | None = None
    disclosure: str | None = None
    rules: str | None = None
    source: str = "manual"
    active: bool = True


def _prog_params(p: ProgramIn):
    return (p.name, p.operator, p.url, p.payout_per_1k, p.currency, p.min_views, p.max_payout,
            json.dumps(p.allowed_platforms), json.dumps(p.required_tags), p.required_credit, p.disclosure,
            p.rules, p.source, int(p.active))


@app.get("/programs", dependencies=A)
def list_programs():
    rows = db.q("SELECT * FROM programs ORDER BY payout_per_1k DESC")
    for r in rows:
        r["streamers"] = db.q("""SELECT s.id, s.display_name, s.platform FROM streamers s
                                 JOIN streamer_programs sp ON sp.streamer_id=s.id WHERE sp.program_id=?""", (r["id"],))
    return rows


@app.post("/programs", dependencies=A)
def create_program(p: ProgramIn):
    pid = db.ex("""INSERT INTO programs(name, operator, url, payout_per_1k, currency, min_views, max_payout,
                   allowed_platforms, required_tags, required_credit, disclosure, rules, source, active, verified_at,
                   joined, joined_at, status, field_sources)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'), 1, date('now'), 'active', '{}')""",
                _prog_params(p))
    filled = {k: v for k, v in p.model_dump().items() if v not in (None, [], "") and k not in ("source", "active")}
    importer.set_fields(pid, filled)        # everything typed in counts as confirmed by you
    return {"id": pid}


@app.put("/programs/{pid}", dependencies=A)
def update_program(pid: int, p: ProgramIn):
    old = db.q1("SELECT * FROM programs WHERE id=?", (pid,))
    if not old:
        raise HTTPException(404)
    new = {"name": p.name, "operator": p.operator, "url": p.url, "payout_per_1k": p.payout_per_1k,
           "currency": p.currency, "min_views": p.min_views, "max_payout": p.max_payout,
           "allowed_platforms": p.allowed_platforms, "required_tags": p.required_tags,
           "required_credit": p.required_credit, "disclosure": p.disclosure, "rules": p.rules, "active": int(p.active)}
    changed = {k: v for k, v in new.items()
               if (json.dumps(v) if isinstance(v, list) else v) != old.get(k)}
    importer.set_fields(pid, changed)       # edited fields count as confirmed by you
    return {"ok": True}


@app.delete("/programs/{pid}", dependencies=A)
def delete_program(pid: int):
    db.ex("DELETE FROM programs WHERE id=?", (pid,))
    return {"ok": True}


@app.post("/streamers/{sid}/programs/{pid}", dependencies=A)
def link_program(sid: int, pid: int):
    # Linked by hand = confirmed. Being in a program you joined counts as permission (unless prohibited).
    matching.link(pid, sid, 1.0, "Linked by you", confirmed=True)
    db.ex("UPDATE streamers SET permission_status='program_member' WHERE id=? AND permission_status='unknown'", (sid,))
    return {"ok": True}


@app.delete("/streamers/{sid}/programs/{pid}", dependencies=A)
def unlink_program(sid: int, pid: int):
    db.ex("DELETE FROM streamer_programs WHERE streamer_id=? AND program_id=?", (sid, pid))
    return {"ok": True}


# ---- projects & jobs ----------------------------------------------------------------
class ProjectIn(BaseModel):
    name: str
    game_id: int | None
    streamer_ids: list[int]
    duration_sec: int
    output_settings: dict = {}
    start: bool = True
    confirm_no_payout: bool = False     # clipping streamers without an active campaign needs a yes


@app.post("/projects", dependencies=A)
def create_project(p: ProjectIn):
    if not db.get_setting("disclaimer_accepted"):
        raise HTTPException(400, "Accept the disclaimer first.")
    blocked = [compliance.can_clip(s)[1] for s in p.streamer_ids if not compliance.can_clip(s)[0]]
    if blocked:
        raise HTTPException(400, {"message": "Some streamers can't be clipped yet.", "reasons": blocked})
    unpaid = [db.q1("SELECT display_name FROM streamers WHERE id=?", (s,))["display_name"]
              for s in p.streamer_ids if not matching.active_campaigns_for(s)]
    if unpaid and not p.confirm_no_payout:
        raise HTTPException(409, {"message": "These streamers have no active campaign, so their clips won't be "
                                             "paid: " + ", ".join(unpaid) + ". Clip them anyway?",
                                  "needs_confirmation": unpaid, "reasons": []})
    filled = autofill.fill(p.streamer_ids, p.output_settings.get("platforms"))
    out = autofill.output_settings(p.output_settings, filled)
    autofill.remember({**p.output_settings, "duration_sec": p.duration_sec})
    with db.tx():
        pid = db.ex("INSERT INTO projects(name, game_id, duration_sec, output_settings) VALUES (?,?,?,?)",
                    (p.name, p.game_id, p.duration_sec, json.dumps(out)))
        for s in p.streamer_ids:
            db.ex("INSERT INTO project_streamers VALUES (?,?)", (pid, s))
    if p.start:
        eng.start_project(pid)
    return {"id": pid}


@app.get("/projects", dependencies=A)
def list_projects():
    rows = db.q("""SELECT p.*, g.name game_name,
                   (SELECT COUNT(*) FROM clips c WHERE c.project_id=p.id) clip_count
                   FROM projects p LEFT JOIN games g ON g.id=p.game_id ORDER BY p.id DESC""")
    for r in rows:
        r["output_settings"] = json.loads(r["output_settings"])
    return rows


@app.post("/projects/{pid}/{action}", dependencies=A)
def project_action(pid: int, action: str):
    fn = {"start": eng.start_project, "pause": eng.pause_project, "resume": eng.resume_project,
          "stop": eng.stop_project}.get(action)
    if not fn:
        raise HTTPException(404)
    fn(pid)
    return {"ok": True}


@app.get("/jobs", dependencies=A)
def jobs(active: bool = True, limit: int = 100):
    where = "WHERE status IN ('queued','running','paused')" if active else ""
    return db.q(f"SELECT id, project_id, kind, status, progress, eta_sec, attempts, error, updated_at "
                f"FROM jobs {where} ORDER BY id DESC LIMIT ?", (limit,))


@app.post("/jobs/{jid}/retry", dependencies=A)
def retry_job(jid: int):
    db.ex("UPDATE jobs SET status='queued', attempts=0, run_after=datetime('now') WHERE id=?", (jid,))
    return {"ok": True}


# ---- clips ---------------------------------------------------------------------------
@app.get("/clips", dependencies=A)
def clips(game_id: int | None = None, streamer_id: int | None = None, review_status: str | None = None,
          posted: bool | None = None, project_id: int | None = None, limit: int = 200):
    cond, params = ["c.deleted_at IS NULL"], []
    for col, val in (("c.game_id", game_id), ("c.streamer_id", streamer_id),
                     ("c.review_status", review_status), ("c.project_id", project_id)):
        if val is not None:
            cond.append(f"{col}=?")
            params.append(val)
    if posted is not None:
        cond.append(("" if posted else "NOT ") + "EXISTS (SELECT 1 FROM posts p WHERE p.clip_id=c.id AND p.status='posted')")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    return db.q(f"""SELECT c.*, s.display_name streamer, g.name game, pr.name program, m.score,
        (SELECT COALESCE(SUM(pm.views),0) FROM posts p JOIN post_metrics pm ON pm.post_id=p.id
           WHERE p.clip_id=c.id AND pm.taken_at=(SELECT MAX(taken_at) FROM post_metrics WHERE post_id=p.id)) views,
        (SELECT COALESCE(SUM(amount),0) FROM payouts py JOIN posts p ON p.id=py.post_id WHERE p.clip_id=c.id) earnings
        FROM clips c LEFT JOIN streamers s ON s.id=c.streamer_id LEFT JOIN games g ON g.id=c.game_id
        LEFT JOIN programs pr ON pr.id=c.program_id LEFT JOIN moments m ON m.id=c.moment_id
        {where} ORDER BY c.id DESC LIMIT ?""", (*params, limit))


class ClipPatch(BaseModel):
    review_status: str | None = None
    title: str | None = None
    description: str | None = None
    hook_text: str | None = None
    hashtags: list[str] | None = None


@app.patch("/clips/{cid}", dependencies=A)
def patch_clip(cid: int, p: ClipPatch):
    for col in ("review_status", "title", "description", "hook_text"):
        val = getattr(p, col)
        if val is not None:
            db.ex(f"UPDATE clips SET {col}=? WHERE id=?", (val, cid))
    if p.hashtags is not None:
        db.ex("UPDATE clips SET hashtags=? WHERE id=?", (json.dumps(p.hashtags), cid))
    if p.review_status == "rejected":
        db.ex("UPDATE moments SET status='rejected' WHERE id=(SELECT moment_id FROM clips WHERE id=?)", (cid,))
    return db.q1("SELECT * FROM clips WHERE id=?", (cid,))


class ExportIn(BaseModel):
    dest: str | None = None


@app.post("/clips/{cid}/export", dependencies=A)
def export(cid: int, body: ExportIn):
    dest = Path(body.dest) if body.dest else storage.media_dir() / "exports"
    return pipeline.export_clip(cid, dest)


@app.get("/clips/{cid}/checklist", dependencies=A)
def checklist(cid: int):
    return posting.submission_checklist(cid)


@app.get("/media", dependencies=A)
def media(path: str):
    """Serve thumbnails/videos for the UI — only from inside the media folder."""
    p = Path(path).resolve()
    root = storage.media_dir().resolve()
    if root not in p.parents or not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


# ---- v0.4: captions, Review editing, feedback, highlight weights, music, trending sounds -------------
from .editing import captions as capmod  # noqa: E402
from .highlights import learning  # noqa: E402
from .music import library as musiclib, trending as trendmod  # noqa: E402


@app.get("/captions/presets", dependencies=A)
def caption_presets():
    return {"presets": capmod.presets_public(), "default": (db.get_setting("captions") or {}).get("style", "bold_pop"),
            "safe_zone": capmod.SAFE, "canvas": [capmod.W, capmod.H]}


@app.get("/fonts/{name}")
def font_file(name: str):
    """Bundled caption fonts for the Review preview (public: fonts are OFL, nothing secret)."""
    d = capmod.fonts_dir()
    if not d or name not in capmod.FONTS.values():
        raise HTTPException(404)
    return FileResponse(d / name, media_type="font/ttf")


def _clip(cid: int) -> dict:
    c = db.q1("SELECT * FROM clips WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404)
    return c


@app.get("/clips/{cid}/edit", dependencies=A)
def clip_edit_info(cid: int):
    """What Review needs to preview and edit captions on the actual clip."""
    c = _clip(cid)
    d = _edit_render_view(cid) or json.loads(c.get("render_spec") or "{}")
    ev = Path(c["file_path"]).with_suffix(".events.json") if c.get("file_path") else None
    fb = db.q1("SELECT rating, tags FROM clip_feedback WHERE clip_id=?", (cid,))
    track = musiclib.get(c["music_track_id"]) if c.get("music_track_id") else None
    return {
        "clip_id": cid, "editable": bool(d.get("base") and Path(d["base"]).exists()),
        "base_path": d.get("base"), "words": json.loads(c.get("words") or "[]"), "offset": d.get("start", 0.0),
        "events": json.loads(ev.read_text(encoding="utf-8")) if ev and ev.exists() else [],
        "preset": c.get("caption_preset") or d.get("caption_style") or "bold_pop", "nudge": c.get("caption_nudge") or 0,
        "moment_type": c.get("moment_type"), "moment_reason": c.get("moment_reason"),
        "music": {"title": track["title"], "licence": track["licence"], "source": track["source"],
                  "plan": json.loads(c.get("music_plan") or "null")} if track else None,
        "original_audio_only": bool(c.get("original_audio_only")),
        "nomusic_path": c.get("nomusic_path"),
        "feedback": {"rating": fb["rating"], "tags": json.loads(fb["tags"])} if fb else None,
        "feedback_tags": learning.TAGS,
        "trending": trendmod.suggest(c.get("moment_type") or "Funny"),
    }


class RerenderIn(BaseModel):
    words: list[dict] | None = None
    caption_nudge: float | None = None
    caption_style: str | None = None
    uppercase: bool | None = None
    emoji: bool | None = None
    profanity: str | None = None
    hook: str | None = None
    original_audio_only: bool | None = None


def _edit_render_view(cid: int) -> dict | None:
    """For edit-project clips: the same shape Review's caption studio reads from v0.4 clips."""
    from .edit import compile as ec, project as ep
    p = ep.get(cid)
    if not p:
        return None
    e = p["edl"]
    try:
        key = ec.base_key(e)
    except Exception:
        return None
    base = storage.media_dir() / "edit_cache" / f"c{cid}" / f"base_{key}.mp4"
    if not base.exists():
        m = db.q1("SELECT moment_id FROM clips WHERE id=?", (cid,))
        alt = storage.media_dir() / "edit_cache" / f"m{m['moment_id']}" / f"base_{key}.mp4" if m else None
        base = alt if alt and alt.exists() else base
    tmpl = e["captions"]["template"]
    first = (e["layout"]["keyframes"] or [{"layout": "blur_fill"}])[0]["layout"]
    return {"base": str(base) if base.exists() else (e["source"]["path"] if Path(e["source"]["path"]).exists() else None),
            "start": e["range"][0], "caption_style": tmpl if isinstance(tmpl, str) else "bold_pop",
            "layout": "crop" if first in ("speaker", "pip") else first, "keep_clear": e["rules"].get("onscreen"),
            "music": e["music"].get("path") if e["music"].get("enabled") else None}


@app.post("/clips/{cid}/rerender", dependencies=A)
def clip_rerender(cid: int, b: RerenderIn):
    c = _clip(cid)
    d = _edit_render_view(cid) or json.loads(c.get("render_spec") or "{}")
    if not d.get("base") or not Path(d["base"]).exists():
        raise HTTPException(409, "This clip was made before captions could be edited (or its base file was "
                                 "cleaned up). New clips can be re-rendered.")
    changes = {k: v for k, v in b.model_dump().items() if v is not None}
    if "words" in changes:
        for w in changes["words"]:
            if not {"word", "start", "end"} <= set(w):
                raise HTTPException(400, "Each word needs word, start and end.")
    if "caption_nudge" in changes:
        changes["caption_nudge"] = max(-400.0, min(400.0, float(changes["caption_nudge"])))
    jid = eng.enqueue("rerender_clip", {"clip_id": cid, "changes": changes}, c["project_id"], priority=1)
    return {"job_id": jid}


@app.post("/clips/{cid}/caption-preview", dependencies=A)
def clip_caption_preview(cid: int, b: RerenderIn):
    """Caption layout (no render) for the live preview while you nudge or edit words."""
    import tempfile
    c = _clip(cid)
    d = _edit_render_view(cid) or json.loads(c.get("render_spec") or "{}")
    words = b.words if b.words is not None else json.loads(c.get("words") or "[]")
    meta = {}
    if d.get("base") and Path(d["base"]).with_suffix(".json").exists():
        meta = json.loads(Path(d["base"]).with_suffix(".json").read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp()) / "p.ass"
    tr = capmod.build_track(words, tmp, b.caption_style or d.get("caption_style", "bold_pop"), d.get("words_per_chunk"),
                            offset=d.get("start", 0.0), layout=d.get("layout") or "crop", src_w=meta.get("src_w", 1920),
                            src_h=meta.get("src_h", 1080), keep_clear=tuple(d.get("keep_clear") or ()),
                            nudge=b.caption_nudge if b.caption_nudge is not None else (c.get("caption_nudge") or 0),
                            uppercase=b.uppercase if b.uppercase is not None else d.get("uppercase"),
                            profanity=d.get("profanity", "keep"), fillers=d.get("fillers", "remove"))
    return {"events": tr.events, "preset": capmod.preset(b.caption_style or d.get("caption_style")).key}


class FeedbackIn(BaseModel):
    rating: int
    tags: list[str] = []


@app.post("/clips/{cid}/feedback", dependencies=A)
def clip_feedback(cid: int, b: FeedbackIn):
    _clip(cid)
    if b.rating not in (-1, 1):
        raise HTTPException(400, "rating is 1 (thumbs up) or -1 (thumbs down)")
    res = learning.record(cid, b.rating, b.tags)
    n = db.q1("SELECT COUNT(*) n FROM clip_feedback")["n"]
    return {**res, "ratings": n, "needed": learning.MIN_RATINGS}


@app.post("/clips/{cid}/export-for-trending", dependencies=A)
def clip_export_trending(cid: int):
    c = _clip(cid)
    jid = eng.enqueue("export_for_trending", {"clip_id": cid}, c["project_id"], priority=1)
    return {"job_id": jid}


@app.get("/highlights/weights", dependencies=A)
def highlight_weights():
    w = learning.all_weights()
    hs = db.get_setting("highlights", {}) or {}
    names = {}
    for scope in w["learned"]:
        kind, sid = scope.split(":", 1)
        row = db.q1("SELECT display_name n FROM streamers WHERE id=?", (sid,)) if kind == "streamer" else \
            db.q1("SELECT name n FROM games WHERE id=?", (sid,))
        names[scope] = row["n"] if row else scope
    return {**w, "names": names, "focus": hs.get("focus", "balanced"), "min_ratings": learning.MIN_RATINGS,
            "ratings": db.q1("SELECT COUNT(*) n FROM clip_feedback")["n"]}


@app.put("/highlights/weights", dependencies=A)
def highlight_weights_set(body: dict):
    if "weights" in body:
        from .highlights.detect import DEFAULT_WEIGHTS
        cur = {**DEFAULT_WEIGHTS, **(db.get_setting("highlight_weights") or {})}
        w = {k: max(0.0, float(body["weights"].get(k, cur[k]))) for k in DEFAULT_WEIGHTS}
        tot = sum(w.values()) or 1.0
        db.set_setting("highlight_weights", {k: round(v / tot, 3) for k, v in w.items()})
    if "focus" in body:
        if body["focus"] not in ("balanced", "funny", "action"):
            raise HTTPException(400, "focus is balanced, funny or action")
        db.set_setting("highlights", {**(db.get_setting("highlights", {}) or {}), "focus": body["focus"]})
    return highlight_weights()


@app.post("/highlights/weights/reset", dependencies=A)
def highlight_weights_reset(body: dict):
    learning.reset(body.get("scope"))
    return highlight_weights()


@app.get("/music/tracks", dependencies=A)
def music_tracks():
    try:
        musiclib.ensure_bundled()
    except Exception as e:
        db.log("warn", "music", f"Bundled music couldn't be built: {e}")
    return {"tracks": musiclib.tracks(), "settings": db.get_setting("music", {}) or {},
            "local_warning": musiclib.LOCAL_LICENCE}


@app.post("/music/scan", dependencies=A)
def music_scan(body: dict):
    folder = body.get("folder") or (db.get_setting("music", {}) or {}).get("folder")
    if not folder:
        raise HTTPException(400, "Choose your music folder first.")
    try:
        res = musiclib.scan_local(folder)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    db.set_setting("music", {**(db.get_setting("music", {}) or {}), "folder": folder})
    return res


@app.patch("/music/tracks/{tid}", dependencies=A)
def music_edit(tid: int, body: dict):
    if not musiclib.get(tid):
        raise HTTPException(404)
    return musiclib.edit(tid, body)


@app.put("/music/settings", dependencies=A)
def music_settings(body: dict):
    cur = db.get_setting("music", {}) or {}
    for k in ("enabled", "duck_db", "folder"):
        if k in body:
            cur[k] = body[k]
    if "duck_db" in cur:
        cur["duck_db"] = max(12.0, min(18.0, float(cur["duck_db"])))
    db.set_setting("music", cur)
    return cur


@app.get("/trending", dependencies=A)
def trending_list():
    return trendmod.listing(active_only=False)


@app.post("/trending/import", dependencies=A)
def trending_import(body: dict):
    if not (body.get("text") or "").strip():
        raise HTTPException(400, "Paste one sound per line.")
    return trendmod.import_text(body["text"])


@app.delete("/trending/{tid}", dependencies=A)
def trending_delete(tid: int):
    db.ex("UPDATE trending_sounds SET active=0 WHERE id=?", (tid,))
    return {"ok": True}


# ---- v0.5: edit projects (EDL), transcript editor, reframe, caption templates -------------------------
from .edit import build as edit_build, compile as edit_compile, project as edit_project  # noqa: E402
from .edit import reframe as edit_reframe, templates as edit_templates, timeline as edit_tl  # noqa: E402
from .edit import transcript as edit_tr  # noqa: E402


def _ep(cid: int) -> dict:
    _clip(cid)
    p = edit_build.ensure_project(cid)
    if not p:
        raise HTTPException(409, "This clip can't be edited: its source file is gone (it was cleaned up or made "
                                 "before v0.4).")
    return p


def _view(p: dict) -> dict:
    e = p["edl"]
    words = e["captions"]["words"]
    proxy = db.get_setting(f"proxy:{p['clip_id']}") or {}
    return {**p, "duration": edit_tl.duration(e), "kept": edit_tl.kept(e),
            "words_out": [{"i": i, **w, "out": edit_tl.to_out(e, float(w["start"]))} for i, w in enumerate(words)],
            "fillers": edit_tr.filler_indices(e), "silences": edit_tr.silences(e),
            "layout_segments": edit_compile.layout_segments(e),
            "rule_fixes": edit_reframe.enforce_rules(e["layout"]["keyframes"], e["rules"].get("onscreen") or [],
                                                     e["layout"].get("facecam"))[1],
            "proxy": proxy.get("path") if proxy.get("head") == p["head"] and proxy.get("path") and Path(proxy["path"]).exists() else None}


@app.get("/edit/{cid}", dependencies=A)
def edit_get(cid: int):
    return _view(_ep(cid))


class EdlIn(BaseModel):
    edl: dict
    label: str = "Edit"


@app.put("/edit/{cid}", dependencies=A)
def edit_put(cid: int, b: EdlIn):
    cur = _ep(cid)
    e = b.edl
    for k in ("source", "range", "cuts", "layout", "captions", "music", "rules"):
        if k not in e:
            raise HTTPException(400, f"Edit project is missing '{k}'.")
    e["source"] = cur["edl"]["source"]                       # the source can't be swapped from the UI
    if edit_tl.duration(e) < 1:
        raise HTTPException(400, "The clip has to be at least 1 second long.")
    return _view(edit_project.save(cid, e, b.label[:60]))


@app.post("/edit/{cid}/undo", dependencies=A)
def edit_undo(cid: int):
    _ep(cid)
    return _view(edit_project.undo(cid))


@app.post("/edit/{cid}/redo", dependencies=A)
def edit_redo(cid: int):
    _ep(cid)
    return _view(edit_project.redo(cid))


@app.get("/edit/{cid}/history", dependencies=A)
def edit_history(cid: int):
    _ep(cid)
    return edit_project.history(cid)


@app.post("/edit/{cid}/transcript", dependencies=A)
def edit_transcript(cid: int, body: dict):
    """op: delete | restore (indices) · fillers · silences · range (start, end, source time) · fix (index, text)."""
    _ep(cid)
    op = body.get("op")
    try:
        if op == "delete":
            fn, label = (lambda e: edit_tr.delete_words(e, body.get("indices", []))), "Delete words"
        elif op == "restore":
            fn, label = (lambda e: edit_tr.restore_words(e, body.get("indices", []))), "Restore words"
        elif op == "fillers":
            fn, label = edit_tr.remove_fillers, "Remove filler words"
        elif op == "silences":
            fn, label = (lambda e: edit_tr.remove_silences(e, float(body.get("min_gap", edit_tr.SILENCE)))), "Remove silences"
        elif op == "range":
            fn, label = (lambda e: edit_tr.set_range(e, body["start"], body["end"])), "Change clip range"
        elif op == "fix":
            fn, label = (lambda e: edit_tr.fix_word(e, int(body["index"]), str(body["text"]))), "Fix word"
        else:
            raise HTTPException(400, "op is delete, restore, fillers, silences, range or fix")
        p = edit_project.update(cid, fn, label)
    except (ValueError, KeyError, IndexError) as e:
        raise HTTPException(400, str(e))
    if edit_tl.duration(p["edl"]) < 1:
        edit_project.undo(cid)
        raise HTTPException(400, "That would leave less than 1 second of clip.")
    return _view(p)


@app.post("/edit/{cid}/reframe", dependencies=A)
def edit_reframe_auto(cid: int):
    """Re-run AI reframe on the current range (keeps your facecam box)."""
    p = _ep(cid)
    e = p["edl"]
    fc = e["layout"].get("facecam")
    from .editing.layout import Box
    lay = edit_reframe.analyse(Path(e["source"]["path"]), e["range"][0], e["range"][1],
                               facecam=Box(**fc) if fc else None, onscreen=e["rules"].get("onscreen") or [],
                               speech=[(w["start"], w["end"]) for w in e["captions"]["words"]], detect_cam=fc is None)
    return _view(edit_project.update(cid, lambda x: {**x, "layout": {**x["layout"], **lay}}, "AI reframe"))


@app.post("/edit/{cid}/facecam", dependencies=A)
def edit_facecam(cid: int, body: dict):
    """Set the facecam box (fractions of the source frame); remember=True saves it for the streamer."""
    _ep(cid)
    box = {k: float(body[k]) for k in ("x", "y", "w", "h")}
    if not (0 <= box["x"] < 1 and 0 <= box["y"] < 1 and 0.03 < box["w"] <= 1 and 0.03 < box["h"] <= 1
            and box["x"] + box["w"] <= 1.001 and box["y"] + box["h"] <= 1.001):
        raise HTTPException(400, "The facecam box has to be inside the video.")

    def apply(e):
        e["layout"]["facecam"] = box
        for k in e["layout"]["keyframes"]:
            if k["layout"] in ("split", "pip") or k.get("cam"):
                k["cam"] = box
        return e
    out = edit_project.update(cid, apply, "Move facecam")
    if body.get("remember", True):
        c = db.q1("SELECT streamer_id FROM clips WHERE id=?", (cid,))
        if c and c["streamer_id"]:
            db.set_setting(f"facecam:{c['streamer_id']}", box)
    return _view(out)


@app.post("/edit/{cid}/fix-rules", dependencies=A)
def edit_fix_rules(cid: int):
    p = _ep(cid)
    e = p["edl"]
    kfs, fixed = edit_reframe.enforce_rules(e["layout"]["keyframes"], e["rules"].get("onscreen") or [], e["layout"].get("facecam"))
    if not fixed:
        return _view(p)
    return _view(edit_project.update(cid, lambda x: {**x, "layout": {**x["layout"], "keyframes": kfs}}, "Fix campaign rules"))


@app.post("/edit/{cid}/translate", dependencies=A)
def edit_translate(cid: int, body: dict):
    from .edit import translate as edit_trans
    p = _ep(cid)
    lang = body.get("language")
    if not lang:
        return _view(edit_project.update(cid, lambda e: {**e, "captions": {**e["captions"], "translate_to": None}},
                                         "Original-language captions"))
    try:
        tr = edit_trans.translate_words(p["edl"]["captions"]["words"], lang, p["edl"]["captions"].get("language"))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))

    def apply(e):
        e["captions"].setdefault("translations", {})[lang] = tr
        e["captions"]["translate_to"] = lang
        return e
    return _view(edit_project.update(cid, apply, f"Captions in {edit_trans.LANGUAGES[lang]}"))


@app.post("/edit/{cid}/preview", dependencies=A)
def edit_preview(cid: int):
    """Fast low-res preview render of the current edit (background job)."""
    _ep(cid)
    c = db.q1("SELECT project_id FROM clips WHERE id=?", (cid,))
    out = storage.media_dir() / "edit_cache" / f"c{cid}" / "preview.mp4"
    return {"job_id": eng.enqueue("export_edit", {"clip_id": cid, "quality": "proxy", "out": str(out)},
                                  c["project_id"], priority=0)}


@app.post("/edit/{cid}/export", dependencies=A)
def edit_export(cid: int, body: dict):
    _ep(cid)
    preset = body.get("preset") or "tiktok"
    if preset not in edit_compile.PRESETS:
        raise HTTPException(400, f"preset is one of {', '.join(edit_compile.PRESETS)}")
    c = db.q1("SELECT project_id FROM clips WHERE id=?", (cid,))
    return {"job_id": eng.enqueue("export_edit", {"clip_id": cid, "preset": preset, "music": body.get("music", True)},
                                  c["project_id"], priority=1)}


# ---- v0.6: Editor list, scores, rule fixes, fix my clip, batch, trends --------------------------------
EDITOR_SORT = {"profit": "c.profit_likely DESC NULLS LAST, c.virality DESC", "virality": "c.virality DESC NULLS LAST",
               "newest": "c.id DESC"}


@app.get("/edit/{cid}/canvas", dependencies=A)
def edit_canvas(cid: int):
    """What the Editor's live preview needs: the cut + reframed base video with no captions (if built), and the
    caption layout for the current edit, so caption changes show instantly without a render."""
    import tempfile
    from .editing import captions as capmod
    p = _ep(cid)
    e = p["edl"]
    try:
        key = edit_compile.base_key(e)
    except Exception:                                   # noqa: BLE001
        key = None
    base = None
    m = db.q1("SELECT moment_id FROM clips WHERE id=?", (cid,))
    dirs = [storage.media_dir() / "edit_cache" / f"c{cid}"] + (
        [storage.media_dir() / "edit_cache" / f"m{m['moment_id']}"] if m and m["moment_id"] else [])
    for d in dirs:
        for suffix in ("p", ""):
            f = d / f"base_{key}{suffix}.mp4"
            if key and f.exists():
                base = f
                break
        if base:
            break
    meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8")) if base and base.with_suffix(".json").exists() \
        else {"layout": next((("crop" if lay in ("speaker", "pip") else lay) for _, lay in edit_compile.layout_segments(e)),
                             "blur_fill"),
              "src_w": e["source"].get("width", 1920), "src_h": e["source"].get("height", 1080)}
    spec = edit_compile.spec_for(e, Path(tempfile.mkdtemp()) / "o.mp4", base or Path("base.mp4"), "proxy")
    tmpl = spec.template or spec.caption_style
    events = []
    if spec.captions_enabled:
        try:
            tr = capmod.build_track(spec.words, Path(tempfile.mkdtemp()) / "c.ass", tmpl, spec.words_per_chunk,
                                    layout_segments=spec.layout_segments, offset=0.0, layout=meta.get("layout", "blur_fill"),
                                    src_w=meta.get("src_w", 1920), src_h=meta.get("src_h", 1080),
                                    keep_clear=tuple(spec.keep_clear), nudge=spec.caption_nudge, uppercase=spec.uppercase,
                                    profanity=spec.profanity, fillers=spec.fillers, emoji=spec.emoji, hook=spec.hook,
                                    hook_sec=spec.hook_sec, credit=spec.credit, duration=edit_tl.duration(e))
            events = tr.events
        except Exception as ex:                         # noqa: BLE001
            log.warning("caption layout for clip %s failed: %s", cid, ex)
    style = capmod.template_from(tmpl).as_dict()
    return {"base": str(base) if base else None, "events": events, "style": style, "duration": edit_tl.duration(e),
            "head": p["head"]}


@app.get("/editor/clips", dependencies=A)
def editor_clips(sort: str = "profit", min_score: int | None = None, program_id: int | None = None,
                 streamer_id: int | None = None, moment_type: str | None = None, status: str | None = None,
                 project_id: int | None = None, limit: int = 300):
    if sort not in EDITOR_SORT:
        raise HTTPException(400, "sort is profit, virality or newest")
    cond, params = ["c.deleted_at IS NULL", "c.review_status != 'rejected'"], []
    for col, val in (("c.program_id", program_id), ("c.streamer_id", streamer_id), ("c.project_id", project_id),
                     ("m.type", moment_type)):
        if val is not None:
            cond.append(f"{col}=?")
            params.append(val)
    if min_score is not None:
        cond.append("c.virality >= ?")
        params.append(min_score)
    rows = db.q(f"""SELECT c.id, c.title, c.file_path, c.thumb_path, c.virality, c.virality_reason, c.virality_parts,
        c.profit_low, c.profit_likely, c.profit_high, c.profit_platform, c.expected_views, c.rule_flags, c.review_status,
        c.streamer_id, c.program_id, c.project_id, c.duration_sec, s.display_name streamer, pr.name program,
        m.type moment_type, m.reason moment_reason, ep.status edit_status, ep.updated_at edited_at,
        EXISTS (SELECT 1 FROM posts p WHERE p.clip_id=c.id AND p.status='posted') posted
        FROM clips c LEFT JOIN streamers s ON s.id=c.streamer_id LEFT JOIN programs pr ON pr.id=c.program_id
        LEFT JOIN moments m ON m.id=c.moment_id LEFT JOIN edit_projects ep ON ep.clip_id=c.id
        WHERE {" AND ".join(cond)} ORDER BY {EDITOR_SORT[sort]} LIMIT ?""", (*params, limit))
    out = []
    for r in rows:
        st = "posted" if r["posted"] else (r["edit_status"] or "new")
        if status and st != status:
            continue
        flags = json.loads(r["rule_flags"] or "[]")
        out.append({**r, "status": st, "rule_flags": flags, "virality_parts": json.loads(r["virality_parts"] or "{}"),
                    "rules_ok": not any(f.get("severity") == "error" for f in flags)})
    if sort == "profit":
        from .scoring import clipscore
        out = clipscore.rank_for_posting(out)
    return out


@app.post("/edit/{cid}/score", dependencies=A)
def edit_score(cid: int):
    from .scoring import clipscore
    p = _ep(cid)
    return clipscore.score_clip(cid, p["edl"])


@app.post("/edit/{cid}/fix", dependencies=A)
def edit_fix(cid: int, body: dict):
    """One-click fix for one campaign-rule flag ({"id": ...}) or all of them ({"all": true})."""
    from .scoring import rules_check
    _ep(cid)
    if body.get("all"):
        return _view(edit_project.update(cid, lambda e: rules_check.fix_all(e)[0], "Fix campaign rules"))
    fid = body.get("id")
    if not fid:
        raise HTTPException(400, "id or all is required")
    return _view(edit_project.update(cid, lambda e: rules_check.fix(e, fid), f"Fix: {fid}"))


@app.post("/edit/{cid}/fix-my-clip", dependencies=A)
def edit_fix_my_clip(cid: int):
    from .scoring import fixclip
    p = _ep(cid)
    e, done = fixclip.fix_my_clip(p["edl"])
    v = _view(edit_project.save(cid, e, "Fix my clip: " + ", ".join(done)) if done else p)
    return {**v, "changes": done}


@app.post("/edit/{cid}/auto-edit", dependencies=A)
def edit_auto(cid: int):
    """Back to the automatic edit (first version), then the one-click clean-up."""
    from .scoring import fixclip
    p = _ep(cid)
    first = db.q1("SELECT data FROM edit_versions WHERE project_id=? ORDER BY version LIMIT 1", (p["id"],))
    e = json.loads(first["data"]) if first else p["edl"]
    e, done = fixclip.fix_my_clip(e)
    return {**_view(edit_project.save(cid, e, "Auto-edit")), "changes": done}


@app.post("/edit/{cid}/signal", dependencies=A)
def edit_signal(cid: int, body: dict):
    """Implicit learning signals from the Editor: keep, delete, edit, export."""
    kind = body.get("kind")
    if kind not in ("keep", "delete", "edit", "export"):
        raise HTTPException(400, "kind is keep, delete, edit or export")
    _clip(cid)
    db.ex("INSERT INTO clip_signals(clip_id, kind) VALUES (?,?)", (cid, kind))
    if kind == "delete":
        db.ex("UPDATE clips SET deleted_at=datetime('now') WHERE id=?", (cid,))
    elif kind == "keep":
        db.ex("UPDATE clips SET review_status='approved' WHERE id=? AND review_status='pending'", (cid,))
    return {"ok": True}


@app.post("/editor/batch", dependencies=A)
def editor_batch(body: dict):
    """Apply one action to many clips: template, captions (regenerate), fix, export, delete."""
    ids = [int(i) for i in body.get("ids") or []]
    action = body.get("action")
    if not ids or action not in ("template", "captions", "fix", "export", "delete", "keep"):
        raise HTTPException(400, "ids and action (template, captions, fix, export, delete, keep) are required")
    from .scoring import fixclip
    done, failed, jobs = [], [], []
    for cid in ids:
        try:
            if action == "template":
                t = body.get("template")
                if not t or not edit_templates.get(t):
                    raise HTTPException(400, "unknown template")
                _ep(cid)
                edit_project.update(cid,
                                    lambda e: {**e, "captions": {**e["captions"], "template": t}}, f"Template: {t}")
            elif action == "captions":
                _ep(cid)
                edit_project.update(cid, lambda e: {**e, "captions": {**e["captions"], "enabled": True, "nudge": 0.0,
                                                                         "translate_to": None}}, "Regenerate captions")
            elif action == "fix":
                p = _ep(cid)
                e, ch = fixclip.fix_my_clip(p["edl"])
                if ch:
                    edit_project.save(cid, e, "Fix my clip: " + ", ".join(ch))
            elif action == "export":
                jobs.append(edit_export(cid, {"preset": body.get("preset") or "tiktok", "music": body.get("music", True)})["job_id"])
            else:
                edit_signal(cid, {"kind": action})
            done.append(cid)
        except HTTPException as e:
            if e.status_code == 400:
                raise
            failed.append({"id": cid, "error": e.detail})
    return {"done": done, "failed": failed, "jobs": jobs}


@app.get("/trends", dependencies=A)
def trends():
    return db.q("SELECT * FROM trend_topics ORDER BY active DESC, id DESC")


@app.post("/trends", dependencies=A)
def trends_add(body: dict):
    """Topics you're watching (game updates, memes, events): one per line. Used by the virality score."""
    n = 0
    for line in str(body.get("text") or "").splitlines():
        t = line.strip().lstrip("#").strip()
        if 1 < len(t) <= 60:
            db.ex("INSERT OR IGNORE INTO trend_topics(topic) VALUES (?)", (t,))
            n += 1
    return {"added": n, "topics": trends()}


@app.delete("/trends/{tid}", dependencies=A)
def trends_delete(tid: int):
    db.ex("DELETE FROM trend_topics WHERE id=?", (tid,))
    return {"ok": True}


@app.get("/export-presets", dependencies=A)
def export_presets():
    return edit_compile.PRESETS


@app.get("/caption-templates", dependencies=A)
def caption_templates():
    from .editing import captions as cm
    return {"templates": edit_templates.all_templates(), "defaults": edit_templates.defaults(),
            "fonts": [{"family": f, "file": n} for f, n in cm.FONTS.items()], "animations": cm.ANIMATIONS}


@app.post("/caption-templates", dependencies=A)
def caption_template_save(body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Give the template a name.")
    return edit_templates.save(name[:40], body.get("template") or {}, body.get("id"))


@app.delete("/caption-templates/{tid}", dependencies=A)
def caption_template_delete(tid: int):
    edit_templates.delete(tid)
    return {"ok": True}


@app.put("/caption-templates/defaults", dependencies=A)
def caption_template_defaults(body: dict):
    scope = body.get("scope", "global")
    if not re.fullmatch(r"global|account:\d+|program:\d+", scope):
        raise HTTPException(400, "scope is global, account:<id> or program:<id>")
    return edit_templates.set_default(scope, body.get("key"))


# ---- dashboard / analytics / logs -----------------------------------------------------
@app.get("/dashboard", dependencies=A)
def dashboard():
    return {
        "clips": db.q1("""SELECT COUNT(*) total, SUM(review_status='pending') pending,
                          SUM(review_status='approved') approved FROM clips"""),
        "projects_active": db.q("SELECT id, name, status, ends_at FROM projects WHERE status IN ('running','paused')"),
        "jobs_running": db.q("""SELECT id, kind, progress, eta_sec, project_id FROM jobs
                                WHERE status='running' ORDER BY id"""),
        "earnings": db.q("""SELECT pr.name program, py.status, py.currency, SUM(py.amount) amount
                            FROM payouts py JOIN programs pr ON pr.id=py.program_id
                            GROUP BY pr.name, py.status, py.currency"""),
        "disk": storage.disk_status(),
        **_home_extras(),
    }


def _home_extras() -> dict:
    rate = float(db.get_setting("usd_to_aud", 1.53))
    week = db.q1("""SELECT COALESCE(SUM(COALESCE(amount_aud, amount * ?)),0) v FROM payouts
                    WHERE status IN ('estimated','pending','approved','paid')
                    AND COALESCE(paid_at, submitted_at) >= datetime('now','-7 days')""", (rate,))
    est = db.q1("""SELECT COALESCE(SUM(projected_aud),0) v FROM posts WHERE status='posted'
                   AND posted_at >= datetime('now','-7 days')""")
    to_submit = db.q1("SELECT COUNT(*) n FROM submissions WHERE status='todo'")
    projects = db.q("""SELECT p.id, p.name, p.status, p.started_at, p.ends_at, p.duration_sec,
                       (SELECT COUNT(*) FROM clips c WHERE c.project_id=p.id) clips
                       FROM projects p WHERE p.status IN ('running','paused') ORDER BY p.id DESC""")
    for p in projects:
        p["progress"] = db.q1("""SELECT CASE WHEN ? IS NULL OR ? IS NULL THEN 0 ELSE
                                 MIN(1.0, MAX(0.0, (julianday('now') - julianday(?)) / (julianday(?) - julianday(?))))
                                 END v""", (p["started_at"], p["ends_at"], p["started_at"], p["ends_at"],
                                             p["started_at"]))["v"]
    return {"earnings_week_aud": round(max(week["v"] or 0, est["v"] or 0), 2), "to_submit": to_submit["n"],
            "projects_progress": projects}


@app.get("/logs", dependencies=A)
def logs(level: str | None = None, limit: int = 500):
    if level:
        return db.q("SELECT * FROM logs WHERE level=? ORDER BY id DESC LIMIT ?", (level, limit))
    return db.q("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))


@app.get("/events", dependencies=A)
async def events():
    """Server-sent events -> desktop toasts in the UI."""
    async def gen():
        while True:
            try:
                ev = eng.EVENTS.get_nowait()
                yield f"data: {json.dumps(ev)}\n\n"
            except Exception:
                await asyncio.sleep(0.5)
                yield ": keepalive\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


# ---- OAuth (YouTube upload account) ---------------------------------------------------
@app.get("/oauth/youtube/start", dependencies=A)
def yt_start():
    from .posting.youtube import auth_url
    try:
        return {"url": auth_url()}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/oauth/youtube/callback")   # opened by the system browser; protected by OAuth state instead
def yt_callback(code: str = Query(...), state: str = Query(...)):
    from .posting.youtube import exchange_code
    try:
        res = exchange_code(code, state)
    except Exception as e:
        return HTMLResponse(f"<h2>YouTube sign-in failed</h2><p>{html.escape(redact(e))}</p>", status_code=400)
    aid = connect_account("youtube", res["handle"], res["channel_id"])
    secrets.set_secret(f"account:{aid}", res["token"])
    return HTMLResponse("<h2>YouTube connected to ClipFarm. You can close this tab.</h2>")


def connect_account(platform: str, handle: str, external_id: str) -> int:
    """Attach a sign-in to the right accounts row. Preloaded handles (no external id yet) are claimed
    instead of creating a duplicate. (The old code relied on lastrowid after an UPSERT, which can
    return a different row's id and file the token under the wrong account.)"""
    norm = handle.lstrip("@").lower()
    row = db.q1("SELECT id FROM accounts WHERE platform=? AND external_id=?", (platform, external_id)) or \
        db.q1("SELECT id FROM accounts WHERE platform=? AND external_id IS NULL AND lower(ltrim(handle,'@'))=?",
              (platform, norm))
    if row:
        db.ex("UPDATE accounts SET handle=?, external_id=?, oauth_status='connected' WHERE id=?",
              (handle, external_id, row["id"]))
        return row["id"]
    return db.ex("INSERT INTO accounts(platform, handle, external_id, oauth_status) VALUES (?,?,?,'connected')",
                 (platform, handle, external_id))


@app.get("/accounts", dependencies=A)
def accounts():
    return db.q("SELECT * FROM accounts")


@app.patch("/accounts/{aid}", dependencies=A)
def patch_account(aid: int, body: dict):
    for col in ("handle", "followers", "max_posts_per_day"):
        if col in body:
            db.ex(f"UPDATE accounts SET {col}=? WHERE id=?", (body[col], aid))
    return db.q1("SELECT * FROM accounts WHERE id=?", (aid,))


# ---- setup health ------------------------------------------------------------------------
@app.get("/health/checks", dependencies=A)
def health_checks():
    return {"checks": healthmod.latest(), "summary": healthmod.summary()}


@app.post("/health/run", dependencies=A)
def health_run(body: dict | None = None):
    res = healthmod.run_all((body or {}).get("only"))
    return {"checks": healthmod.latest(), "summary": healthmod.summary(), "ran": len(res)}


@app.post("/health/fix/{fix_id}", dependencies=A)
def health_fix(fix_id: str):
    out = healthmod.fix(fix_id)
    healthmod.run_all()
    return {**out, "checks": healthmod.latest(), "summary": healthmod.summary()}


@app.get("/health/diagnostics", dependencies=A)
def diagnostics():
    path = healthmod.write_diagnostics()
    return {"text": path.read_text(encoding="utf-8"), "path": str(path)}


# ---- backup: export / import keys + settings (password-protected) ----------------------------
class BackupIn(BaseModel):
    password: str
    path: str | None = None
    data: str | None = None          # base64 file contents (alternative to path)


@app.post("/backup/export", dependencies=A)
def backup_export(b: BackupIn):
    ids = [a["id"] for a in db.q("SELECT id FROM accounts")]
    try:
        blob = vault.export_bundle(secrets.all_secrets(ids), db.all_settings(), b.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if b.path:
        Path(b.path).write_bytes(blob)
        return {"ok": True, "path": b.path}
    return {"ok": True, "data": base64.b64encode(blob).decode("ascii")}


@app.post("/backup/import", dependencies=A)
def backup_import(b: BackupIn):
    raw = Path(b.path).read_bytes() if b.path else base64.b64decode(b.data or "")
    try:
        bundle = vault.import_bundle(raw, b.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    for name, value in bundle.get("secrets", {}).items():
        secrets.set_secret(name, value)
    keep_local = {"media_dir"}          # folder paths belong to the old PC
    for k, v in bundle.get("settings", {}).items():
        if k not in keep_local and not k.startswith(("facecam:", "yt_discovery_at:")):
            db.set_setting(k, v)
    return {"ok": True, "secrets": len(bundle.get("secrets", {})), "settings": len(bundle.get("settings", {}))}


# ---- campaigns -------------------------------------------------------------------------------
def _campaign_row(r: dict) -> dict:
    for k in ("allowed_platforms", "required_tags", "required_onscreen", "needs_confirming", "channel_hints",
              "field_sources", "last_changes"):
        try:
            r[k] = json.loads(r[k]) if r.get(k) else ([] if k not in ("channel_hints", "field_sources") else {})
        except ValueError:
            pass
    r["streamers"] = db.q("""SELECT s.id, s.display_name, s.platform, s.login, sp.match_confidence, sp.matched_by,
                             sp.confirmed FROM streamers s JOIN streamer_programs sp ON sp.streamer_id=s.id
                             WHERE sp.program_id=?""", (r["id"],))
    pj = db.q("SELECT * FROM projections WHERE program_id=? ORDER BY aud_per_pc_hour DESC", (r["id"],))
    for x in pj:
        x["warnings"] = json.loads(x["warnings"] or "[]")
        x["details"] = json.loads(x["details"] or "{}")
    r["projections"] = pj
    r["best"] = pj[0] if pj else None
    return r


@app.get("/campaigns", dependencies=A)
def campaigns(include_inactive: bool = False):
    where = "" if include_inactive else "WHERE active=1"
    return [_campaign_row(r) for r in db.q(f"SELECT * FROM programs {where} ORDER BY joined DESC, name")]


class ImportIn(BaseModel):
    text: str
    url: str | None = None
    operator: str = "content_rewards"
    joined: bool = True


@app.post("/campaigns/import", dependencies=A)
def campaign_import(b: ImportIn):
    parsed = importer.parse(b.text, b.url)
    if not parsed.get("_sources") and not parsed.get("external_id"):
        raise HTTPException(400, "Couldn't find any campaign details in that text. Copy the whole campaign page "
                                 "(Ctrl+A, Ctrl+C) and paste it again.")
    pid = importer.upsert(parsed, b.operator, b.joined)
    res = matching.match_program(pid)
    matching.sync_permissions()
    return {"id": pid, "found": sorted(parsed.get("_sources", {})), "match": res,
            "campaign": _campaign_row(db.q1("SELECT * FROM programs WHERE id=?", (pid,)))}


class PageIn(BaseModel):
    """Sent by the in-app browser ONLY when the user presses "Import this page": the visible page's
    URL, text and campaign links. ClipFarm never fetches Whop / Content Rewards pages itself."""
    url: str
    text: str
    title: str = ""
    links: list[dict] = []


@app.post("/campaigns/import-page", dependencies=A)
def campaign_import_page(b: PageIn):
    if len(b.text) > 400_000:
        raise HTTPException(413, "That page is too big to import.")
    src = importer.PageSource(importer.PageInput(url=b.url, text=b.text, title=b.title, links=b.links[:200]))
    summary = importer.run_import(src)
    for item in summary.new + summary.updated:
        matching.match_program(item["id"]) if item["joined"] else None
    matching.sync_permissions()
    return summary.as_dict()


@app.post("/campaigns/import-paste", dependencies=A)
def campaign_import_paste(b: ImportIn):
    summary = importer.run_import(importer.PasteSource(b.text, b.url))
    if not summary.total:
        raise HTTPException(400, "Couldn't find any campaign details in that text. Copy the whole campaign page "
                                 "(Ctrl+A, Ctrl+C) and paste it again.")
    matching.sync_permissions()
    return summary.as_dict()


@app.post("/campaigns/{pid}/joined", dependencies=A)
def campaign_set_joined(pid: int, body: dict):
    """The user ticks "I've joined" after clicking Join themselves on the site."""
    if not db.q1("SELECT 1 FROM programs WHERE id=?", (pid,)):
        raise HTTPException(404)
    j = bool(body.get("joined", True))
    db.ex("UPDATE programs SET joined=?, joined_at=CASE WHEN ? THEN COALESCE(joined_at, date('now')) ELSE joined_at END "
          "WHERE id=?", (int(j), int(j), pid))
    if j:
        matching.match_program(pid)
    matching.sync_permissions()
    return _campaign_row(db.q1("SELECT * FROM programs WHERE id=?", (pid,)))


@app.patch("/campaigns/{pid}", dependencies=A)
def campaign_patch(pid: int, body: dict):
    if not db.q1("SELECT 1 FROM programs WHERE id=?", (pid,)):
        raise HTTPException(404)
    importer.set_fields(pid, body)
    matching.sync_permissions()
    return _campaign_row(db.q1("SELECT * FROM programs WHERE id=?", (pid,)))


@app.post("/campaigns/match", dependencies=A)
def campaign_match():
    return {"results": matching.match_all(), "pending": matching.pending_matches()}


@app.get("/campaigns/matches", dependencies=A)
def campaign_matches():
    return matching.pending_matches()


@app.post("/campaigns/matches/{mid}", dependencies=A)
def campaign_match_decide(mid: int, body: dict):
    try:
        matching.confirm_match(mid, bool(body.get("accept")))
    except KeyError:
        raise HTTPException(404)
    return {"ok": True, "pending": matching.pending_matches()}


@app.post("/streamers/{sid}/match", dependencies=A)
def streamer_match(sid: int):
    """Called when the user clicks a streamer: assigns a confident match, or returns one question."""
    try:
        return matching.match_streamer(sid)
    except KeyError:
        raise HTTPException(404)


@app.post("/streamers/{sid}/match/answer", dependencies=A)
def streamer_match_answer(sid: int, body: dict):
    try:
        return matching.answer(sid, body.get("program_id"))
    except KeyError:
        raise HTTPException(404)


@app.get("/games/{game_id}/streamers/grouped", dependencies=A)
def streamers_grouped(game_id: int):
    return grouping.grouped(game_id)


class FillIn(BaseModel):
    streamer_ids: list[int]
    platforms: list[str] | None = None


@app.get("/projects/defaults", dependencies=A)
def project_defaults():
    return autofill.defaults()


@app.post("/projects/autofill", dependencies=A)
def project_autofill(b: FillIn):
    return autofill.fill(b.streamer_ids, b.platforms)


@app.get("/projections", dependencies=A)
def projections():
    rows = db.q("""SELECT pj.*, s.display_name streamer, s.platform streamer_platform, p.name program,
                   p.campaign_type, p.status FROM projections pj JOIN streamers s ON s.id=pj.streamer_id
                   JOIN programs p ON p.id=pj.program_id ORDER BY pj.aud_per_pc_hour DESC""")
    for x in rows:
        x["warnings"] = json.loads(x["warnings"] or "[]")
        x["details"] = json.loads(x["details"] or "{}")
    return rows


@app.post("/projections/refresh", dependencies=A)
def projections_refresh():
    autopilot.refresh_status()
    forecast.project_all()
    return projections()


@app.post("/forecast/refit", dependencies=A)
def forecast_refit():
    return forecast.refit()


# ---- auto-pilot --------------------------------------------------------------------------------
@app.get("/autopilot", dependencies=A)
def autopilot_get():
    return {"settings": autopilot.settings(), "plan": db.get_setting("autopilot_plan") or [],
            "log": db.q("SELECT * FROM autopilot_log ORDER BY id DESC LIMIT 100")}


@app.put("/autopilot", dependencies=A)
def autopilot_put(body: dict):
    cur = autopilot.settings()
    new = {**cur, **{k: v for k, v in body.items() if k in autopilot.DEFAULTS}}
    db.set_setting("autopilot", new)
    if new["enabled"] and not db.q1("SELECT 1 FROM jobs WHERE kind='autopilot_tick' AND status IN ('queued','running')"):
        eng.enqueue("autopilot_tick", {}, priority=1)
    if not new["enabled"] and cur["enabled"]:
        p = db.q1("SELECT id FROM projects WHERE name=? AND status='running'", (autopilot.PROJECT_NAME,))
        if p:
            eng.pause_project(p["id"])
        autopilot.log("pause", "Auto-pilot switched off by you.")
    return autopilot_get()


@app.post("/autopilot/run", dependencies=A)
def autopilot_run():
    matching.match_all()
    res = autopilot.tick()
    return {**autopilot_get(), "status_changes": res["status_changes"]}


# ---- why a streamer is ranked where it is ---------------------------------------------------
@app.get("/streamers/{sid}/why", dependencies=A)
def streamer_why(sid: int, game_id: int | None = None):
    from .discovery.ranking import WEIGHTS
    s = db.q1("SELECT * FROM streamers WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404)
    r = db.q1("SELECT * FROM rankings WHERE streamer_id=?" + (" AND game_id=?" if game_id else "") +
              " ORDER BY computed_at DESC", (sid, game_id) if game_id else (sid,))
    parts = []
    if r:
        for k, label in (("audience", "Audience size"), ("payout", "Projected earnings (A$/PC-hour)"),
                         ("competition", "Few other clippers"), ("highlight", "How often big moments happen"),
                         ("growth", "Growing viewership")):
            v = r.get(f"{k}_score")
            if v is not None:
                parts.append({"part": k, "label": label, "value": v, "weight": WEIGHTS[k],
                              "points": round(v * WEIGHTS[k] * 100, 1)})
    camps = matching.active_campaigns_for(sid)
    pj = db.q("""SELECT pj.*, p.name program FROM projections pj JOIN programs p ON p.id=pj.program_id
                 WHERE pj.streamer_id=? ORDER BY aud_per_pc_hour DESC""", (sid,))
    for x in pj:
        x["warnings"] = json.loads(x["warnings"] or "[]")
        x["details"] = json.loads(x["details"] or "{}")
    notes = []
    if s["permission_status"] == "unknown":
        notes.append("Permission unknown: score halved and can't be clipped until you record permission.")
    if not camps:
        notes.append("No payout: no active campaign you've joined. Auto-pilot never clips this streamer; "
                     "clipping by hand asks you to confirm first.")
    return {"streamer": s, "score": r["profit_score"] if r else None, "parts": parts, "projections": pj,
            "campaigns": [{"id": c["id"], "name": c["name"], "status": c["status"]} for c in camps],
            "no_payout": not camps, "notes": notes}


# ---- posting & submissions ---------------------------------------------------------------------
class ScheduleIn(BaseModel):
    account_ids: list[int]


@app.post("/clips/{cid}/schedule", dependencies=A)
def clip_schedule(cid: int, b: ScheduleIn):
    try:
        return subs.schedule(cid, b.account_ids)
    except subs.PostingBlocked as e:
        raise HTTPException(409, str(e))


class PostedIn(BaseModel):
    account_id: int
    url: str


@app.post("/clips/{cid}/posted", dependencies=A)
def clip_posted(cid: int, b: PostedIn):
    try:
        return subs.record_posted(cid, b.account_id, b.url)
    except subs.PostingBlocked as e:
        raise HTTPException(409, str(e))


@app.get("/posts", dependencies=A)
def posts(status: str | None = None):
    where = "WHERE p.status=?" if status else ""
    rows = db.q(f"""SELECT p.*, a.platform, a.handle, c.title, c.file_path, c.caption_text, c.moment_type,
                    c.nomusic_path, pr.name program
                    FROM posts p JOIN accounts a ON a.id=p.account_id JOIN clips c ON c.id=p.clip_id
                    LEFT JOIN programs pr ON pr.id=p.program_id {where} ORDER BY p.scheduled_at""",
                (status,) if status else ())
    from .music import trending as tm
    for r in rows:      # a trending sound to add in the app when posting (names only, never embedded)
        if r["platform"] in ("tiktok", "instagram"):
            s = tm.suggest(r.get("moment_type") or "Funny", r["platform"], 1)
            r["trending"] = s[0] if s else None
    return rows


@app.post("/posts/{post_id}/cancel", dependencies=A)
def post_cancel(post_id: int):
    db.ex("UPDATE posts SET status='cancelled' WHERE id=? AND status IN ('scheduled','failed')", (post_id,))
    return {"ok": True}


@app.get("/submissions", dependencies=A)
def submissions_list(status: str | None = "todo"):
    return subs.checklist(status if status != "all" else None)


@app.post("/submissions/{sub_id}", dependencies=A)
def submission_mark(sub_id: int, body: dict):
    try:
        return subs.mark_submitted(sub_id, bool(body.get("done", True)))
    except subs.AlreadySubmitted as e:
        raise HTTPException(409, str(e))
    except KeyError:
        raise HTTPException(404)


class PayoutIn(BaseModel):
    status: str
    amount: float | None = None
    currency: str = "USD"


@app.post("/posts/{post_id}/payout", dependencies=A)
def record_payout(post_id: int, b: PayoutIn):
    if b.status not in ("pending", "approved", "paid", "rejected"):
        raise HTTPException(400, "bad status")
    post = db.q1("SELECT * FROM posts WHERE id=?", (post_id,))
    if not post or not post["program_id"]:
        raise HTTPException(404, "Post isn't linked to a campaign")
    rate = float(db.get_setting("usd_to_aud", 1.53))
    aud = None if b.amount is None else (b.amount * rate if b.currency == "USD" else b.amount)
    if db.q1("SELECT 1 FROM payouts WHERE post_id=?", (post_id,)):
        db.ex("""UPDATE payouts SET status=?, amount=COALESCE(?, amount), currency=?, amount_aud=COALESCE(?, amount_aud),
                 paid_at=CASE WHEN ?='paid' THEN datetime('now') ELSE paid_at END WHERE post_id=?""",
              (b.status, b.amount, b.currency, aud, b.status, post_id))
    else:
        db.ex("""INSERT INTO payouts(program_id, post_id, amount, currency, amount_aud, status) VALUES (?,?,?,?,?,?)""",
              (post["program_id"], post_id, b.amount or 0, b.currency, aud, b.status))
    return {"ok": True}


class MetricIn(BaseModel):
    views: int
    likes: int | None = None


@app.post("/posts/{post_id}/metrics", dependencies=A)
def record_metric(post_id: int, b: MetricIn):
    """Views typed in by hand for platforms ClipFarm can't read (TikTok, Instagram, X)."""
    db.ex("INSERT OR REPLACE INTO post_metrics(post_id, views, likes) VALUES (?,?,?)", (post_id, b.views, b.likes))
    return {"ok": True}


@app.get("/earnings", dependencies=A)
def earnings():
    rate = float(db.get_setting("usd_to_aud", 1.53))
    by_status = db.q("""SELECT status, ROUND(SUM(COALESCE(amount_aud, amount * ?)),2) aud, COUNT(*) n
                        FROM payouts GROUP BY status""", (rate,))
    projected = db.q("""SELECT p.name program, pj.platform, s.display_name streamer, pj.per_day_likely, pj.d7_low,
                        pj.d7_likely, pj.d7_high, pj.d30_low, pj.d30_likely, pj.d30_high
                        FROM projections pj JOIN programs p ON p.id=pj.program_id
                        JOIN streamers s ON s.id=pj.streamer_id ORDER BY pj.d30_likely DESC""")
    model = db.get_setting("fitted_model")
    return {"usd_to_aud": rate, "by_status": by_status, "est_vs_actual": forecast.estimated_vs_actual(),
            "projected": projected, "model": model or {"fitted": False}}
