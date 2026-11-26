"""TikTok via the official Content Posting API.

Default: Upload (inbox / draft) mode. ClipFarm uploads the finished video; TikTok sends a notification to
the TikTok app's inbox; you open it, add a trending sound if you like, paste the caption and press Post.
Why: an unaudited developer app can only Direct Post as private (SELF_ONLY), and a clipping app is unlikely
to pass TikTok's audit. Direct Post stays in the code, switched off (setting posting.tiktok_direct), for the
day the app is audited.

Limits (TikTok docs, checked Nov 2026):
  - inbox uploads: 5 pending shares per user in any 24 hours (error spam_risk_too_many_pending_share);
    ClipFarm counts its own and queues the rest
  - 6 init requests per minute per user token
  - the inbox video init takes no caption, so the caption is copied to your clipboard and shown on screen
Sign-in: Login Kit for Desktop (PKCE, loopback redirect http://127.0.0.1:<port>/oauth/tiktok/callback/).
"""
from __future__ import annotations

import hashlib
import secrets as pysecrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .. import config, db, secrets
from .common import NeedsUser, check_cancel

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
TOKEN_URL = API + "/v2/oauth/token/"
INBOX_INIT = API + "/v2/post/publish/inbox/video/init/"
DIRECT_INIT = API + "/v2/post/publish/video/init/"
CREATOR_INFO = API + "/v2/post/publish/creator_info/query/"
STATUS = API + "/v2/post/publish/status/fetch/"
DAILY_DRAFTS = 5
MIN_CHUNK, CHUNK = 5 * 1024 * 1024, 10 * 1024 * 1024
_pending: dict[str, str] = {}          # state -> code_verifier


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)      # naive UTC, like SQLite's datetime('now')


class DraftsFull(RuntimeError):
    """5 drafts already sent in the last 24 hours: wait (queued), not an error."""
    def __init__(self, msg: str, next_free: datetime):
        super().__init__(msg)
        self.next_free = next_free


def redirect_uri() -> str:
    return f"http://127.0.0.1:{config.API_PORT}/oauth/tiktok/callback/"


def direct_enabled() -> bool:
    return bool(db.get_setting("posting", {}).get("tiktok_direct", False))


def auth_url() -> str:
    key = secrets.get_secret("tiktok_client_key")
    if not key:
        raise NeedsUser("Add your TikTok Client key and secret in Settings > Connections first.")
    verifier = pysecrets.token_urlsafe(48)
    state = pysecrets.token_urlsafe(16)
    _pending[state] = verifier
    scope = "user.info.basic,video.upload" + (",video.publish" if direct_enabled() else "")
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": key, "response_type": "code", "scope": scope, "redirect_uri": redirect_uri(),
        "state": state, "code_challenge": hashlib.sha256(verifier.encode()).hexdigest(),
        "code_challenge_method": "S256"})


def _api_error(r: requests.Response) -> tuple[str, str]:
    try:
        e = r.json().get("error") or {}
        return e.get("code") or "", e.get("message") or ""
    except ValueError:
        return "", r.text[:200]


def exchange_code(code: str, state: str) -> dict:
    verifier = _pending.pop(state, None)
    if not verifier:
        raise RuntimeError("This TikTok sign-in link has expired (ClipFarm was restarted). Click Connect again.")
    r = requests.post(TOKEN_URL, timeout=20, headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
        "client_key": secrets.get_secret("tiktok_client_key"), "client_secret": secrets.get_secret("tiktok_client_secret"),
        "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri(), "code_verifier": verifier})
    tok = r.json() if r.ok else {}
    if not tok.get("access_token"):
        raise RuntimeError(f"TikTok sign-in failed: {tok.get('error_description') or r.text[:200]}")
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 86400))
    tok["refresh_expires_at"] = time.time() + int(tok.get("refresh_expires_in", 365 * 86400))
    me = requests.get(API + "/v2/user/info/", params={"fields": "open_id,display_name"}, timeout=20,
                      headers={"Authorization": f"Bearer {tok['access_token']}"})
    user = ((me.json() if me.ok else {}).get("data") or {}).get("user") or {}
    return {"token": tok, "open_id": tok.get("open_id") or user.get("open_id"),
            "name": user.get("display_name") or "TikTok", "scope": tok.get("scope", "")}


def access_token(account_id: int) -> str:
    tok = secrets.get_secret(f"account:{account_id}", as_json=True)
    if not tok or "refresh_token" not in tok:
        raise NeedsUser("This TikTok account isn't signed in. Settings > Connections > Connect TikTok.")
    if time.time() > tok.get("expires_at", 0) - 120:
        r = requests.post(TOKEN_URL, timeout=20, headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
            "client_key": secrets.get_secret("tiktok_client_key"),
            "client_secret": secrets.get_secret("tiktok_client_secret"),
            "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        new = r.json() if r.ok else {}
        if not new.get("access_token"):
            db.ex("UPDATE accounts SET oauth_status='needs_reconnect' WHERE id=?", (account_id,))
            raise NeedsUser("TikTok sign-in expired or was removed. Reconnect TikTok in Settings > Connections.")
        tok.update(access_token=new["access_token"], refresh_token=new.get("refresh_token", tok["refresh_token"]),
                   expires_at=time.time() + int(new.get("expires_in", 86400)))
        secrets.set_secret(f"account:{account_id}", tok)
    return tok["access_token"]


# ---- the 5-a-day draft limit -----------------------------------------------------------------------------------
def drafts(account_id: int, now: datetime | None = None) -> dict:
    now = now or _utcnow()
    since = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db.q("""SELECT sent_at FROM posts WHERE account_id=? AND stage='inbox' AND sent_at >= ?
                   ORDER BY sent_at""", (account_id, since))
    used = len(rows)
    nxt = (datetime.fromisoformat(rows[0]["sent_at"]) + timedelta(hours=24)) if used >= DAILY_DRAFTS else now
    return {"used": used, "limit": DAILY_DRAFTS, "left": max(0, DAILY_DRAFTS - used), "next_free": nxt.isoformat(),
            "label": f"{min(used, DAILY_DRAFTS)} of {DAILY_DRAFTS} TikTok drafts used today"}


def _chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count): small files go up whole; otherwise 10 MB chunks, and the last chunk
    takes the remainder (TikTok: chunks 5-64 MB, the final one may be bigger)."""
    if size <= CHUNK + MIN_CHUNK:
        return size, 1
    return CHUNK, size // CHUNK


def _send(upload_url: str, video: Path, size: int, chunk: int, count: int, post_id: int | None) -> None:
    for i in range(count):
        check_cancel(post_id)
        start = i * chunk
        end = size - 1 if i == count - 1 else start + chunk - 1
        with open(video, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        r = requests.put(upload_url, data=data, timeout=600, headers={
            "Content-Type": "video/mp4", "Content-Length": str(len(data)),
            "Content-Range": f"bytes {start}-{end}/{size}"})
        if r.status_code not in (200, 201, 206):
            raise RuntimeError(f"TikTok upload failed (chunk {i + 1}/{count}): HTTP {r.status_code}")


def send_to_inbox(account_id: int, video: Path, post_id: int | None = None) -> dict:
    """Upload a finished clip to the user's TikTok inbox. Returns {"publish_id"}."""
    d = drafts(account_id)
    if d["left"] <= 0:
        raise DraftsFull(f"{d['label']}; the next draft goes when a slot frees up.",
                         datetime.fromisoformat(d["next_free"]))
    size = video.stat().st_size
    chunk, count = _chunk_plan(size)
    r = requests.post(INBOX_INIT, timeout=30, json={"source_info": {
        "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}},
        headers={"Authorization": f"Bearer {access_token(account_id)}", "Content-Type": "application/json; charset=UTF-8"})
    code, msg = _api_error(r)
    if code == "spam_risk_too_many_pending_share":
        raise DraftsFull("TikTok says 5 drafts are already waiting in your inbox; queued until one is posted or 24 h pass.",
                         _utcnow() + timedelta(hours=6))
    if code == "access_token_invalid" or r.status_code == 401:
        raise NeedsUser("TikTok sign-in expired. Reconnect TikTok in Settings > Connections.")
    if code == "scope_not_authorized":
        raise NeedsUser("ClipFarm's TikTok app doesn't have the video.upload permission yet. "
                        "Add Content Posting API (Upload) to your TikTok developer app and reconnect.")
    if code not in ("", "ok") or not r.ok:
        raise RuntimeError(f"TikTok refused the upload: {msg or code or r.status_code}")
    data = r.json()["data"]
    _send(data["upload_url"], video, size, chunk, count, post_id)
    if post_id:
        db.ex("UPDATE posts SET stage='inbox', publish_id=?, sent_at=datetime('now') WHERE id=?",
              (data["publish_id"], post_id))
    return {"publish_id": data["publish_id"]}


def fetch_status(account_id: int, publish_id: str) -> dict:
    """{"status": PROCESSING_UPLOAD | SEND_TO_USER_INBOX | PUBLISH_COMPLETE | FAILED, "post_id": str|None, ...}"""
    r = requests.post(STATUS, json={"publish_id": publish_id}, timeout=20,
                      headers={"Authorization": f"Bearer {access_token(account_id)}",
                               "Content-Type": "application/json; charset=UTF-8"})
    data = (r.json() if r.ok else {}).get("data") or {}
    ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
    return {"status": data.get("status") or "UNKNOWN", "fail_reason": data.get("fail_reason"),
            "post_id": str(ids[0]) if ids else None}


def post_url(handle: str, post_id: str) -> str:
    return f"https://www.tiktok.com/@{handle.lstrip('@')}/video/{post_id}"


# ---- Direct Post (off until the developer app passes TikTok's audit) ----------------------------------------------
def direct_post(account_id: int, video: Path, caption: str, post_id: int | None = None) -> dict:
    if not direct_enabled():
        raise NeedsUser("TikTok Direct Post is switched off (needs an audited TikTok app). Using inbox mode.")
    tok = access_token(account_id)
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"}
    info = requests.post(CREATOR_INFO, headers=h, timeout=20).json().get("data") or {}
    levels = info.get("privacy_level_options") or ["SELF_ONLY"]
    privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in levels else levels[0]
    size = video.stat().st_size
    chunk, count = _chunk_plan(size)
    r = requests.post(DIRECT_INIT, headers=h, timeout=30, json={
        "post_info": {"title": caption[:2200], "privacy_level": privacy, "disable_comment": False,
                      "disable_duet": False, "disable_stitch": False, "is_aigc": False},
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}})
    code, msg = _api_error(r)
    if code not in ("", "ok") or not r.ok:
        raise RuntimeError(f"TikTok refused the post: {msg or code}")
    data = r.json()["data"]
    _send(data["upload_url"], video, size, chunk, count, post_id)
    if post_id:
        db.ex("UPDATE posts SET stage='auto', publish_id=?, sent_at=datetime('now') WHERE id=?",
              (data["publish_id"], post_id))
    return {"publish_id": data["publish_id"], "privacy": privacy}
