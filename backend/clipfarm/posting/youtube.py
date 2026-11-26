"""YouTube Shorts upload via the official YouTube Data API v3 (resumable upload).

OAuth: desktop "installed app" flow with a loopback redirect to the local API
(http://127.0.0.1:<port>/oauth/youtube/callback). Tokens are stored in the OS keychain.
Quota (checked Nov 2026): since 1 June 2026 uploads (videos.insert) have their own bucket of 100 calls a day
per Google Cloud project, reset at midnight Pacific time. ClipFarm counts its own uploads per Pacific day, shows
"N uploads left today", and when the bucket (or Google's quotaExceeded) says no, moves the post to the next day.
Uploads go up in 8 MB chunks and stop between chunks if the post is cancelled (clip deleted).
"""
from __future__ import annotations

import json
import secrets as pysecrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .. import config, secrets
from ..netcheck import explain_response

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
_pending_states: set[str] = set()


class ReconnectNeeded(RuntimeError):
    """The saved YouTube sign-in no longer works (revoked, expired or missing). Permanent: retrying
    won't help until the user reconnects."""
    permanent = True


def redirect_uri() -> str:
    # Desktop-app OAuth clients accept any loopback port, so the per-launch port is fine.
    return f"http://{config.API_HOST}:{config.API_PORT}/oauth/youtube/callback"


REDIRECT = redirect_uri()


def auth_url() -> str:
    cid = secrets.get_secret("google_oauth_client_id")
    if not cid:
        raise RuntimeError("Add your Google OAuth Client ID/Secret in Settings > Connections first.")
    state = pysecrets.token_urlsafe(16)
    _pending_states.add(state)
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect_uri(), "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state})


def exchange_code(code: str, state: str) -> dict:
    if state not in _pending_states:
        raise RuntimeError("This sign-in link has expired (ClipFarm was restarted). Click Connect again.")
    _pending_states.discard(state)
    r = requests.post(TOKEN_URL, data={"code": code, "client_id": secrets.get_secret("google_oauth_client_id"),
                                       "client_secret": secrets.get_secret("google_oauth_client_secret"),
                                       "redirect_uri": redirect_uri(), "grant_type": "authorization_code"},
                      timeout=20)
    p = explain_response("Google sign-in", r)
    if p:
        raise RuntimeError(p.message)
    tok = r.json()
    if "refresh_token" not in tok:
        raise RuntimeError("Google didn't return a refresh token. Remove ClipFarm from "
                           "myaccount.google.com/permissions and connect again.")
    tok["expires_at"] = time.time() + tok["expires_in"]
    tok["connected_at"] = time.time()
    if tok.get("refresh_token_expires_in"):      # Google only sends this for time-limited sign-ins (Testing mode)
        tok["refresh_expires_at"] = time.time() + int(tok["refresh_token_expires_in"])
    me = requests.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                      headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=20).json()
    ch = me["items"][0]
    return {"token": tok, "channel_id": ch["id"], "handle": ch["snippet"].get("customUrl") or ch["snippet"]["title"]}


def access_token(account_id: int) -> str:
    """Valid access token, refreshed when it's within 2 minutes of expiry."""
    tok = secrets.get_secret(f"account:{account_id}", as_json=True)
    if not tok or "refresh_token" not in tok:
        raise ReconnectNeeded("This YouTube channel isn't signed in. Click Connect YouTube channel.")
    if time.time() > tok.get("expires_at", 0) - 120:
        r = requests.post(TOKEN_URL, data={"client_id": secrets.get_secret("google_oauth_client_id"),
                                           "client_secret": secrets.get_secret("google_oauth_client_secret"),
                                           "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"},
                          timeout=20)
        if r.status_code in (400, 401) and "invalid_grant" in r.text:
            _mark(account_id, "needs_reconnect")
            raise ReconnectNeeded("YouTube access was revoked or has expired (Google expires sign-ins for "
                                  "apps in 'Testing' mode after 7 days). Reconnect the channel.")
        p = explain_response("YouTube", r)
        if p:
            raise RuntimeError(p.message)
        new = r.json()
        tok.update(access_token=new["access_token"], expires_at=time.time() + new["expires_in"])
        secrets.set_secret(f"account:{account_id}", tok)
        _mark(account_id, "connected")
    return tok["access_token"]


_access_token = access_token   # old name


def _mark(account_id: int, status: str) -> None:
    from .. import db
    try:
        db.ex("UPDATE accounts SET oauth_status=? WHERE id=?", (status, account_id))
    except Exception:
        pass


def channel_stats(account_id: int, video_ids: list[str]) -> dict[str, dict]:
    """views/likes/comments for uploaded videos (1 quota unit per 50 videos)."""
    out = {}
    headers = {"Authorization": f"Bearer {access_token(account_id)}"}
    for i in range(0, len(video_ids), 50):
        r = requests.get("https://www.googleapis.com/youtube/v3/videos", headers=headers, timeout=20,
                         params={"part": "statistics", "id": ",".join(video_ids[i:i + 50])})
        p = explain_response("YouTube", r)
        if p:
            raise RuntimeError(p.message)
        for v in r.json().get("items", []):
            st = v.get("statistics", {})
            out[v["id"]] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                            "comments": int(st.get("commentCount", 0))}
    return out


# ---- quota: uploads per Pacific day ----------------------------------------------------------------------------
DAILY_UPLOADS = 100           # videos.insert bucket per Google Cloud project (since 1 June 2026)
CHUNK = 8 * 1024 * 1024       # a multiple of 256 KB, as YouTube's resumable protocol requires


class QuotaUsed(RuntimeError):
    """No uploads left today. The post moves to the next quota day (not an error the user must fix)."""
    def __init__(self, msg: str, next_reset: datetime):
        super().__init__(msg)
        self.next_reset = next_reset


def _pacific_offset(utc: datetime) -> timedelta:
    """US Pacific time without a tz database (Windows Python has none): DST from the 2nd Sunday of March
    2:00 to the 1st Sunday of November 2:00 local time."""
    y = utc.year

    def nth_sunday(month: int, n: int) -> datetime:
        d = datetime(y, month, 1, tzinfo=timezone.utc)
        d += timedelta(days=(6 - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    start = nth_sunday(3, 2) + timedelta(hours=10)      # 2:00 PST = 10:00 UTC
    end = nth_sunday(11, 1) + timedelta(hours=9)        # 2:00 PDT = 9:00 UTC
    return timedelta(hours=-7) if start <= utc < end else timedelta(hours=-8)


def quota_day(now: datetime | None = None) -> tuple[str, datetime]:
    """(today's quota day as YYYY-MM-DD in Pacific time, the UTC moment it resets)."""
    now = now or datetime.now(timezone.utc)
    local = now + _pacific_offset(now)
    nxt_local = datetime(local.year, local.month, local.day, tzinfo=timezone.utc) + timedelta(days=1)
    nxt = nxt_local - _pacific_offset(nxt_local - timedelta(hours=8))
    return local.strftime("%Y-%m-%d"), nxt


def quota(now: datetime | None = None) -> dict:
    """Uploads used/left today for this Google Cloud project (all connected channels share it)."""
    from .. import db
    day, reset = quota_day(now)
    lo = reset - timedelta(days=1)
    used = db.q1("""SELECT COUNT(*) n FROM posts p JOIN accounts a ON a.id=p.account_id WHERE a.platform='youtube'
                    AND p.sent_at >= ? AND p.sent_at < ?""",
                 (lo.strftime("%Y-%m-%d %H:%M:%S"), reset.strftime("%Y-%m-%d %H:%M:%S")))["n"]
    limit = int(db.get_setting("posting", {}).get("youtube_daily_uploads", DAILY_UPLOADS))
    hit = db.get_setting("youtube_quota_hit") == day          # Google said quotaExceeded today
    left = 0 if hit else max(0, limit - used)
    return {"day": day, "used": used, "limit": limit, "left": left, "resets_at": reset.isoformat(),
            "label": f"{left} upload{'s' if left != 1 else ''} left today"}


def consent_reminder(account_id: int) -> str | None:
    """Google expires sign-ins after 7 days while the OAuth consent screen is in Testing."""
    tok = secrets.get_secret(f"account:{account_id}", as_json=True) or {}
    if tok.get("refresh_expires_at"):
        when = datetime.fromtimestamp(tok["refresh_expires_at"]).strftime("%a %d %b")
        return (f"Your Google consent screen is still in Testing, so this sign-in expires {when}. "
                "Publish the app (Google Cloud > OAuth consent screen > Publish app) to stop the weekly reconnect.")
    from .. import db
    if db.get_setting("google_consent_published"):
        return None
    if tok.get("connected_at") and time.time() - tok["connected_at"] > 4 * 86400:
        when = datetime.fromtimestamp(tok["connected_at"] + 7 * 86400).strftime("%a %d %b")
        return (f"If your Google consent screen is still in Testing, this sign-in stops working {when}. "
                "Publish the app in Google Cloud, or reconnect each week.")
    return None


def upload(account_id: int, video: Path, title: str, description: str, tags: list[str],
           privacy: str = "public", made_for_kids: bool = False, post_id: int | None = None) -> dict:
    from .common import chunks
    from .. import db
    q = quota()
    if q["left"] <= 0:
        raise QuotaUsed(f"No YouTube uploads left today ({q['used']}/{q['limit']}); queued for tomorrow.",
                        datetime.fromisoformat(q["resets_at"]))
    headers = {"Authorization": f"Bearer {access_token(account_id)}"}
    title = title if "#shorts" in title.lower() else f"{title[:90]} #Shorts"
    if "#shorts" not in description.lower():
        description = f"{description}\n\n#Shorts".strip()
    meta = {"snippet": {"title": title[:100], "description": description[:4900],
                        "tags": [t.lstrip("#") for t in tags][:15], "categoryId": "20"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids}}
    size = video.stat().st_size
    init = requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                         headers={**headers, "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                         data=json.dumps(meta), timeout=30)
    if init.status_code == 403 and "quotaexceeded" in init.text.lower().replace("_", ""):
        db.set_setting("youtube_quota_hit", q["day"])
        raise QuotaUsed("Google says the YouTube upload quota is used up for today; queued for tomorrow.",
                        datetime.fromisoformat(q["resets_at"]))
    p = explain_response("YouTube", init)
    if p:
        raise RuntimeError(p.message)
    url, r = init.headers["Location"], None
    for off, data in chunks(video, CHUNK, post_id):
        r = requests.put(url, data=data, timeout=600, headers={
            "Content-Type": "video/mp4", "Content-Length": str(len(data)),
            "Content-Range": f"bytes {off}-{off + len(data) - 1}/{size}"})
        if r.status_code == 308:            # Resume Incomplete: next chunk
            continue
        p = explain_response("YouTube", r)
        if p:
            raise RuntimeError(p.message)
    if r is None or r.status_code == 308:
        raise RuntimeError("YouTube didn't confirm the upload. It will be retried.")
    if post_id:
        db.ex("UPDATE posts SET sent_at=datetime('now') WHERE id=?", (post_id,))
    vid = r.json()["id"]
    return {"external_id": vid, "url": f"https://youtube.com/shorts/{vid}"}
