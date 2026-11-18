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

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
REDIRECT = f"http://{config.API_HOST}:{config.API_PORT}/oauth/youtube/callback"
_pending_states: set[str] = set()


def auth_url() -> str:
    cid = secrets.get_secret("google_oauth_client_id")
    if not cid:
        raise RuntimeError("Add your Google OAuth Client ID/Secret in Settings > Connections first.")
    state = pysecrets.token_urlsafe(16)
    _pending_states.add(state)
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state})


def exchange_code(code: str, state: str) -> dict:
    if state not in _pending_states:
        raise RuntimeError("OAuth state mismatch")
    _pending_states.discard(state)
    r = requests.post(TOKEN_URL, data={"code": code, "client_id": secrets.get_secret("google_oauth_client_id"),
                                       "client_secret": secrets.get_secret("google_oauth_client_secret"),
                                       "redirect_uri": REDIRECT, "grant_type": "authorization_code"}, timeout=20)
    r.raise_for_status()
    tok = r.json()
    tok["expires_at"] = time.time() + tok["expires_in"]
    me = requests.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                      headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=20).json()
    ch = me["items"][0]
    return {"token": tok, "channel_id": ch["id"], "handle": ch["snippet"].get("customUrl") or ch["snippet"]["title"]}


def _access_token(account_id: int) -> str:
    tok = secrets.get_secret(f"account:{account_id}", as_json=True)
    if time.time() > tok["expires_at"] - 120:
        r = requests.post(TOKEN_URL, data={"client_id": secrets.get_secret("google_oauth_client_id"),
                                           "client_secret": secrets.get_secret("google_oauth_client_secret"),
                                           "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"},
                          timeout=20)
        r.raise_for_status()
        new = r.json()
        tok.update(access_token=new["access_token"], expires_at=time.time() + new["expires_in"])
        secrets.set_secret(f"account:{account_id}", tok)
    return tok["access_token"]


def upload(account_id: int, video: Path, title: str, description: str, tags: list[str],
           privacy: str = "public", made_for_kids: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {_access_token(account_id)}"}
    title = title if "#shorts" in title.lower() else f"{title[:90]} #Shorts"
    meta = {"snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:15],
                        "categoryId": "20"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids}}
    init = requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                         headers={**headers, "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Type": "video/mp4",
                                  "X-Upload-Content-Length": str(video.stat().st_size)},
                         data=json.dumps(meta), timeout=30)
    init.raise_for_status()
    with open(video, "rb") as f:
        r = requests.put(init.headers["Location"], data=f, headers={"Content-Type": "video/mp4"}, timeout=600)
    r.raise_for_status()
    vid = r.json()["id"]
    return {"external_id": vid, "url": f"https://youtube.com/shorts/{vid}"}
