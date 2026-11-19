"""YouTube Shorts upload via the official YouTube Data API v3 (resumable upload).

OAuth: desktop "installed app" flow with a loopback redirect to the local API
(http://127.0.0.1:<port>/oauth/youtube/callback). Tokens are stored in the OS keychain.
Note: each upload costs ~1,600 quota units of the default 10,000/day — about 6 uploads/day
per Google Cloud project unless you request more quota.
"""
from __future__ import annotations

import json
import secrets as pysecrets
import time
import urllib.parse
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


def upload(account_id: int, video: Path, title: str, description: str, tags: list[str],
           privacy: str = "public", made_for_kids: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {access_token(account_id)}"}
    title = title if "#shorts" in title.lower() else f"{title[:90]} #Shorts"
    meta = {"snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:15],
                        "categoryId": "20"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids}}
    init = requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                         headers={**headers, "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Type": "video/mp4",
                                  "X-Upload-Content-Length": str(video.stat().st_size)},
                         data=json.dumps(meta), timeout=30)
    p = explain_response("YouTube", init)
    if p:
        raise RuntimeError(p.message)
    with open(video, "rb") as f:
        r = requests.put(init.headers["Location"], data=f, headers={"Content-Type": "video/mp4"}, timeout=600)
    p = explain_response("YouTube", r)
    if p:
        raise RuntimeError(p.message)
    vid = r.json()["id"]
    return {"external_id": vid, "url": f"https://youtube.com/shorts/{vid}"}
