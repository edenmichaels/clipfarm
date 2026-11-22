"""Setup Health: every check behind the "Fix my setup" button.

Each check returns a green/yellow/red light, a plain-English message and, where possible, a
one-click fix id. Checks run quietly on every start; results go to the health_checks table, the
Logs screen and a diagnostics file (secrets redacted) the user can copy when asking for help.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from . import __version__, config, db, migrations, secrets, vault
from .netcheck import Problem, explain_exception, explain_response
from .redact import redact
from . import proc

TIMEOUT = 8
CHECKS: list[tuple[str, str, Callable[[], dict]]] = []


def check(cid: str, title: str):
    def deco(fn):
        CHECKS.append((cid, title, fn))
        return fn
    return deco


def ok(msg: str) -> dict:
    return {"status": "green", "message": msg, "fix": None}


def warn(msg: str, fix: str | None = None) -> dict:
    return {"status": "yellow", "message": msg, "fix": fix}


def bad(msg: str, fix: str | None = None) -> dict:
    return {"status": "red", "message": msg, "fix": fix}


def from_problem(p: Problem) -> dict:
    return {"status": p.status, "message": p.message, "fix": p.fix}


# ---- engine & storage --------------------------------------------------------------------------
@check("engine", "ClipFarm engine")
def _engine() -> dict:
    conn = db.connect()
    integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
    ver = migrations.current_version(conn)
    if integrity != "ok":
        return bad(f"The database reports damage ({integrity}). Restore from a backup or reinstall.")
    if ver < migrations.LATEST:
        return bad(f"Database is on version {ver}, expected {migrations.LATEST}.", "migrate")
    return ok(f"Engine {__version__} running. Data folder: {config.app_data_dir()} (database v{ver}).")


@check("storage", "Storage")
def _storage() -> dict:
    from .jobs import storage
    try:
        d = storage.media_dir()
        probe = d / ".write_test"
        probe.write_text("x")
        probe.unlink()
    except OSError as e:
        return bad(f"Can't write to the media folder ({e}). Pick another folder in Settings > General.",
                   "open_settings")
    free = shutil.disk_usage(d).free / 1024 ** 3
    if free < 5:
        return bad(f"Only {free:.1f} GB free on the media drive. ClipFarm needs at least 5 GB.", "cleanup")
    if free < 20:
        return warn(f"{free:.1f} GB free on the media drive. Clean up soon.", "cleanup")
    return ok(f"Media folder {d} is writable, {free:.0f} GB free.")


@check("keys", "API keys (Credential Manager + backup)")
def _keys() -> dict:
    vs = vault.status()
    if not secrets.keyring:
        msg = (f"Windows Credential Manager isn't available ({secrets.keychain_error}). Keys are kept only in "
               "the encrypted backup file.")
        return warn(msg) if vs["readable"] else bad(msg + " The backup file can't be read either.", "reset_vault")
    if vs["exists"] and not vs["readable"]:
        return warn("The encrypted key backup couldn't be read (it may come from another PC or Windows account). "
                    "A fresh backup will be made.", "reset_vault")
    return ok(f"Keys are stored in Credential Manager ('{secrets.SERVICE}') with an encrypted backup "
              f"({vs['count']} item(s)) that survives uninstalling.")


@check("ffmpeg", "FFmpeg (video engine)")
def _ffmpeg() -> dict:
    from .editing.ffmpeg_util import ffmpeg_bin, ffprobe_bin
    try:
        ff, _ = ffmpeg_bin(), ffprobe_bin()
    except FileNotFoundError as e:
        return bad(f"{e} It should be in the ClipFarm install folder (bin\\ffmpeg.exe).", "reinstall")
    try:
        out = proc.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return bad(f"FFmpeg is there but won't run ({e}).", "reinstall")
    if " ass " not in out and " subtitles " not in out:
        return bad("This FFmpeg build has no subtitle (libass) support, so captions can't be burned in.", "reinstall")
    return ok(f"Found {ff}.")


@check("font", "Caption font")
def _font() -> dict:
    from .editing.ffmpeg_util import default_font
    f = default_font()
    if not f:
        return warn("The Anton caption font is missing; captions will use a system font.", "reinstall")
    if "Anton" not in f.name:
        return warn(f"Bundled Anton font not found; using {f.name}.", "reinstall")
    return ok(f"Using {f}.")


@check("gpu", "Video encoder")
def _gpu() -> dict:
    from .editing.ffmpeg_util import detect_encoders
    try:
        encs = detect_encoders()
    except Exception as e:
        return bad(f"Couldn't test encoders: {e}")
    gpu = [e["label"] for e in encs if e["available"] and e["codec"] != "libx264"]
    return ok(f"Using {gpu[0]} (GPU).") if gpu else warn("No GPU encoder found; rendering uses the CPU (slower).")


@check("whisper", "Speech model (captions)")
def _whisper() -> dict:
    tcfg = db.get_setting("transcription", {})
    if not tcfg.get("enabled", True):
        return warn("Speech transcription is turned off, so clips have no captions.", "enable_transcription")
    try:
        import faster_whisper  # noqa: F401
    except Exception as e:
        return bad(f"The speech engine didn't load ({e}). Reinstall ClipFarm.", "reinstall")
    from .highlights.transcribe import model_present, models_dir
    size = tcfg.get("model", "small")
    if model_present(size):
        return ok(f"Whisper '{size}' model is installed in {models_dir()}.")
    return warn(f"Whisper '{size}' model isn't downloaded yet (about 0.5 GB). It downloads on first use, "
                "or click Fix to download it now.", "download_model")


# ---- platforms -------------------------------------------------------------------------------
@check("twitch", "Twitch")
def _twitch() -> dict:
    cid, sec = secrets.get_secret("twitch_client_id"), secrets.get_secret("twitch_client_secret")
    if not (cid and sec):
        return bad("Twitch isn't connected. Add your Client ID and Secret in Settings > Connections. "
                   "Twitch is needed to find streamers and clips.", "open_connections")
    try:
        r = requests.post("https://id.twitch.tv/oauth2/token", timeout=TIMEOUT, data={
            "client_id": cid, "client_secret": sec, "grant_type": "client_credentials"})
    except requests.RequestException as e:
        return from_problem(explain_exception("Twitch", e))
    p = explain_response("Twitch", r)
    if p:
        return from_problem(p)
    tok = r.json()["access_token"]
    try:
        r2 = requests.get("https://api.twitch.tv/helix/games/top", params={"first": 1}, timeout=TIMEOUT,
                          headers={"Client-Id": cid, "Authorization": f"Bearer {tok}"})
    except requests.RequestException as e:
        return from_problem(explain_exception("Twitch", e))
    p = explain_response("Twitch", r2)
    return from_problem(p) if p else ok("Connected. App token works and Helix answers.")


@check("youtube", "YouTube (finding streams)")
def _youtube() -> dict:
    key = secrets.get_secret("youtube_api_key")
    if not key:
        return warn("No YouTube API key, so YouTube streamers won't be found.", "open_connections")
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videoCategories", timeout=TIMEOUT,
                         params={"part": "snippet", "regionCode": "AU", "key": key})   # costs 1 quota unit
    except requests.RequestException as e:
        return from_problem(explain_exception("YouTube", e))
    p = explain_response("YouTube", r)
    return from_problem(p) if p else ok("API key works.")


@check("youtube_upload", "YouTube upload account")
def _youtube_upload() -> dict:
    accts = db.q("SELECT * FROM accounts WHERE platform='youtube'")
    if not secrets.get_secret("google_oauth_client_id"):
        return warn("To post Shorts automatically, add a Google OAuth Client ID/Secret (Desktop app) in Settings > "
                    "Connections, then click Connect YouTube channel.", "open_connections")
    connected = [a for a in accts if secrets.has_secret(f"account:{a['id']}")]
    if not connected:
        handles = ", ".join(a["handle"] for a in accts) or "your channel"
        return warn(f"{handles} isn't signed in yet. Click Connect YouTube channel.", "reconnect_youtube")
    from .posting.youtube import ReconnectNeeded, access_token
    for a in connected:
        try:
            access_token(a["id"])
        except ReconnectNeeded as e:
            return bad(str(e), "reconnect_youtube")
        except requests.RequestException as e:
            return from_problem(explain_exception("YouTube", e))
    return ok(f"{', '.join(a['handle'] for a in connected)} signed in; token refreshes automatically. "
              "Upload limit: about 6 Shorts a day on Google's free quota.")


@check("kick", "Kick")
def _kick() -> dict:
    cid, sec = secrets.get_secret("kick_client_id"), secrets.get_secret("kick_client_secret")
    if not (cid and sec):
        return warn("Kick isn't connected, so Kick streamers won't be found. Optional.", "open_connections")
    try:
        r = requests.post("https://id.kick.com/oauth/token", timeout=TIMEOUT, data={
            "grant_type": "client_credentials", "client_id": cid, "client_secret": sec})
    except requests.RequestException as e:
        return from_problem(explain_exception("Kick", e))
    p = explain_response("Kick", r)
    return from_problem(p) if p else ok("Connected. Note: Kick's official API has no clips or VODs, so Kick is "
                                        "used for finding streamers only.")


def _social(platform: str, label: str, why: str) -> dict:
    accts = db.q("SELECT * FROM accounts WHERE platform=?", (platform,))
    handles = ", ".join(a["handle"] for a in accts) or "no account"
    return warn(f"{label} ({handles}): post manually for now. {why} ClipFarm prepares the video and caption and "
                "adds a 'Submit to campaign' checklist item once you paste the post link.")


@check("tiktok", "TikTok posting")
def _tiktok() -> dict:
    return _social("tiktok", "TikTok", "TikTok's Content Posting API only allows private posts until TikTok "
                                       "audits your developer app.")


@check("instagram", "Instagram posting")
def _instagram() -> dict:
    return _social("instagram", "Instagram", "Reels publishing through the Graph API needs a Business/Creator "
                                             "account linked to a Facebook Page and a public video URL.")


@check("x", "X posting")
def _x() -> dict:
    return warn("X posting through the API needs a paid X API plan. Post manually.")


@check("llm", "AI hooks (Ollama)")
def _llm() -> dict:
    cfg = db.get_setting("llm", {})
    if cfg.get("provider", "ollama") != "ollama":
        if not secrets.get_secret("llm_api_key"):
            return warn("Cloud AI is selected but no API key is set. Template hooks are used instead.",
                        "open_connections")
        return ok(f"Cloud model {cfg.get('cloud_model')} selected.")
    base, model = cfg.get("base_url", "http://127.0.0.1:11434"), cfg.get("model", "llama3.1:8b")
    try:
        r = requests.get(f"{base}/api/tags", timeout=4)
        r.raise_for_status()
    except requests.RequestException:
        return warn("Ollama isn't running, so hooks and titles use simple templates. Install/start Ollama "
                    "for better hooks.", "open_ollama")
    names = [m.get("name", "") for m in r.json().get("models", [])]
    if not any(n == model or n.split(":")[0] == model.split(":")[0] for n in names):
        return warn(f"Ollama is running but the model '{model}' isn't downloaded. Click Fix to download it "
                    "(about 5 GB).", "pull_llm")
    return ok(f"Ollama is running with {model}.")


@check("campaigns", "Campaigns (Content Rewards)")
def _campaigns() -> dict:
    rows = db.q("SELECT * FROM programs WHERE active=1 AND joined=1")
    if not rows:
        return warn("No campaigns joined yet. Import one on the Campaigns screen.", "open_campaigns")
    pending = db.q1("SELECT COUNT(*) n FROM campaign_matches WHERE status='pending'")["n"]
    stale = [r["name"] for r in rows if r["last_synced_at"] and
             (datetime.now(timezone.utc).replace(tzinfo=None) -
              datetime.fromisoformat(r["last_synced_at"])).days >= 3 and r["status"] == "active"]
    unconfirmed = sum(1 for r in rows if json.loads(r["needs_confirming"] or "[]"))
    bits = []
    if pending:
        bits.append(f"{pending} streamer match(es) need you to confirm")
    if stale:
        bits.append(f"{len(stale)} campaign(s) not refreshed in 3+ days (budgets may have run out)")
    if unconfirmed:
        bits.append(f"{unconfirmed} campaign(s) have fields marked 'needs confirming'")
    if bits:
        return warn("; ".join(bits) + ". Content Rewards has no public API, so re-import campaign pages "
                    "to refresh them.", "open_campaigns")
    return ok(f"{len(rows)} campaign(s) joined and up to date.")


# ---- running checks ------------------------------------------------------------------------
def run_all(only: list[str] | None = None) -> list[dict]:
    results = []
    for cid, title, fn in CHECKS:
        if only and cid not in only:
            continue
        t0 = time.time()
        try:
            res = fn()
        except Exception as e:     # a broken check must never break the screen
            res = bad(f"Check crashed: {redact(e)}")
        res = {"id": cid, "title": title, **res, "message": redact(res["message"]),
               "ms": int((time.time() - t0) * 1000)}
        db.ex("""INSERT INTO health_checks(id, status, title, message, fix, checked_at)
                 VALUES (?,?,?,?,?, datetime('now')) ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                 title=excluded.title, message=excluded.message, fix=excluded.fix, checked_at=excluded.checked_at""",
              (cid, res["status"], title, res["message"], res["fix"]))
        if res["status"] != "green":
            db.log("warn" if res["status"] == "yellow" else "error", "health", f"{title}: {res['message']}")
        results.append(res)
    write_diagnostics()
    return results


def latest() -> list[dict]:
    order = {cid: i for i, (cid, _, _) in enumerate(CHECKS)}
    rows = db.q("SELECT * FROM health_checks")
    return sorted(rows, key=lambda r: order.get(r["id"], 999))


def summary() -> dict:
    rows = latest()
    red = [r for r in rows if r["status"] == "red"]
    yellow = [r for r in rows if r["status"] == "yellow"]
    return {"red": len(red), "yellow": len(yellow), "green": len(rows) - len(red) - len(yellow),
            "banner": (f"{len(red)} setup problem(s) need attention: " + ", ".join(r["title"] for r in red))
            if red else None}


def diagnostics_text() -> str:
    lines = [f"ClipFarm diagnostics — {datetime.now().isoformat(timespec='seconds')}",
             f"Version: {__version__}", f"OS: {platform.platform()} ({platform.machine()})",
             f"Python: {sys.version.split()[0]}  frozen={getattr(sys, 'frozen', False)}",
             f"Executable: {sys.executable}", f"Resource dir: {os.environ.get('CLIPFARM_RESOURCE_DIR', '-')}",
             f"Data dir: {config.app_data_dir()}", f"Keychain: {secrets.keychain_backend} "
             f"(error: {secrets.keychain_error or 'none'})", ""]
    lines.append("== Checks ==")
    for r in latest():
        lines.append(f"[{r['status'].upper():6}] {r['title']}: {r['message']}  (checked {r['checked_at']})")
    lines.append("")
    lines.append("== Recent errors ==")
    for r in db.q("SELECT ts, level, source, message FROM logs WHERE level IN ('error','warn') "
                  "ORDER BY id DESC LIMIT 40"):
        lines.append(f"{r['ts']} {r['level']:5} {r['source']}: {r['message'][:300]}")
    lines.append("")
    lines.append("== Failed jobs ==")
    for r in db.q("SELECT id, kind, attempts, error FROM jobs WHERE status='failed' ORDER BY id DESC LIMIT 15"):
        lines.append(f"#{r['id']} {r['kind']} x{r['attempts']}: {(r['error'] or '').splitlines()[0][:300]}")
    return redact("\n".join(lines))


def write_diagnostics() -> Path:
    p = config.app_data_dir() / "diagnostics.txt"
    p.write_text(diagnostics_text(), encoding="utf-8")
    return p


# ---- one-click fixes ---------------------------------------------------------------------------
def fix(fix_id: str) -> dict:
    """Fixes the engine can do itself. UI-only fixes (open a screen, reconnect) are handled by the UI."""
    if fix_id == "migrate":
        migrations.run(db.connect())
        return {"ok": True, "message": "Database updated."}
    if fix_id == "cleanup":
        from .jobs import storage
        r = storage.cleanup()
        return {"ok": True, "message": f"Freed {r['freed_bytes'] / 1e9:.1f} GB."}
    if fix_id == "reset_vault":
        p = vault.vault_path()
        if p.exists():
            os.replace(p, p.with_suffix(".unreadable"))
        secrets.restore([a["id"] for a in db.q("SELECT id FROM accounts")])
        return {"ok": True, "message": "Made a fresh key backup from Credential Manager."}
    if fix_id == "download_model":
        from .jobs.engine import enqueue
        enqueue("download_model", {"size": db.get_setting("transcription", {}).get("model", "small")}, priority=2)
        return {"ok": True, "message": "Downloading the speech model in the background."}
    if fix_id == "enable_transcription":
        t = db.get_setting("transcription", {})
        db.set_setting("transcription", {**t, "enabled": True})
        return {"ok": True, "message": "Transcription switched on."}
    if fix_id == "pull_llm":
        cfg = db.get_setting("llm", {})
        from .jobs.engine import enqueue
        enqueue("pull_llm", {"base_url": cfg.get("base_url"), "model": cfg.get("model")}, priority=8)
        return {"ok": True, "message": "Downloading the AI model in Ollama (this can take a while)."}
    if fix_id == "recheck":
        return {"ok": True, "message": "Re-checked."}
    return {"ok": False, "message": "This fix is done from the app screen it opens."}
