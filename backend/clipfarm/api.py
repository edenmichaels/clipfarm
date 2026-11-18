"""Local HTTP API (FastAPI) consumed by the desktop UI.

Bound to 127.0.0.1 only. Every request must carry the per-launch token the Tauri shell
generates (header X-ClipFarm-Token, or ?token= for <img>/<video> URLs) so other local
programs can't drive the app.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__, compliance, db, secrets
from .discovery import service as disc
from .editing.ffmpeg_util import detect_encoders
from .jobs import engine as eng
from .jobs import pipeline, storage
from .posting import platforms as posting
from .providers import available_providers

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
            "secrets": {k: secrets.has_secret(k) for k in SECRET_NAMES}}


@app.get("/settings", dependencies=A)
def get_settings():
    return db.all_settings()


@app.put("/settings", dependencies=A)
def put_settings(body: dict):
    for k, v in body.items():
        db.set_setting(k, v)
    return db.all_settings()


SECRET_NAMES = ["twitch_client_id", "twitch_client_secret", "youtube_api_key", "kick_client_id",
                "kick_client_secret", "google_oauth_client_id", "google_oauth_client_secret", "llm_api_key"]


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
                   allowed_platforms, required_tags, required_credit, disclosure, rules, source, active, verified_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))""", _prog_params(p))
    return {"id": pid}


@app.put("/programs/{pid}", dependencies=A)
def update_program(pid: int, p: ProgramIn):
    db.ex("""UPDATE programs SET name=?, operator=?, url=?, payout_per_1k=?, currency=?, min_views=?, max_payout=?,
             allowed_platforms=?, required_tags=?, required_credit=?, disclosure=?, rules=?, source=?, active=?,
             verified_at=datetime('now') WHERE id=?""", (*_prog_params(p), pid))
    return {"ok": True}


@app.delete("/programs/{pid}", dependencies=A)
def delete_program(pid: int):
    db.ex("DELETE FROM programs WHERE id=?", (pid,))
    return {"ok": True}


@app.post("/streamers/{sid}/programs/{pid}", dependencies=A)
def link_program(sid: int, pid: int):
    db.ex("INSERT OR IGNORE INTO streamer_programs VALUES (?,?)", (sid, pid))
    # Being in a program you joined counts as permission (unless explicitly prohibited).
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


@app.post("/projects", dependencies=A)
def create_project(p: ProjectIn):
    if not db.get_setting("disclaimer_accepted"):
        raise HTTPException(400, "Accept the disclaimer first.")
    blocked = [compliance.can_clip(s)[1] for s in p.streamer_ids if not compliance.can_clip(s)[0]]
    if blocked:
        raise HTTPException(400, {"message": "Some streamers can't be clipped yet.", "reasons": blocked})
    with db.tx():
        pid = db.ex("INSERT INTO projects(name, game_id, duration_sec, output_settings) VALUES (?,?,?,?)",
                    (p.name, p.game_id, p.duration_sec, json.dumps(p.output_settings)))
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
    cond, params = [], []
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
    }


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
    return {"url": auth_url()}


@app.get("/oauth/youtube/callback")   # opened by the system browser; protected by OAuth state instead
def yt_callback(code: str = Query(...), state: str = Query(...)):
    from .posting.youtube import exchange_code
    res = exchange_code(code, state)
    aid = db.ex("""INSERT INTO accounts(platform, handle, external_id) VALUES ('youtube', ?, ?)
                   ON CONFLICT(platform, external_id) DO UPDATE SET handle=excluded.handle""",
                (res["handle"], res["channel_id"]))
    aid = aid or db.q1("SELECT id FROM accounts WHERE platform='youtube' AND external_id=?", (res["channel_id"],))["id"]
    secrets.set_secret(f"account:{aid}", res["token"])
    return HTMLResponse("<h2>YouTube connected to ClipFarm. You can close this tab.</h2>")


@app.get("/accounts", dependencies=A)
def accounts():
    return db.q("SELECT * FROM accounts")
