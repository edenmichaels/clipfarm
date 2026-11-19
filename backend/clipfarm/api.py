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
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__, compliance, db, secrets, vault
from . import health as healthmod
from .campaigns import autopilot, forecast, importer, matching
from .discovery import service as disc
from .posting import submissions as subs
from .redact import redact
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
              "field_sources"):
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
    return db.q(f"""SELECT p.*, a.platform, a.handle, c.title, c.file_path, c.caption_text, pr.name program
                    FROM posts p JOIN accounts a ON a.id=p.account_id JOIN clips c ON c.id=p.clip_id
                    LEFT JOIN programs pr ON pr.id=p.program_id {where} ORDER BY p.scheduled_at""",
                (status,) if status else ())


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
