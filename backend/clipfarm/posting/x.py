"""X (Twitter): direct posting only with your own paid X API keys; otherwise the Post Kit.

Keys (Settings > Connections > X): API key + secret (the app's consumer keys) and an Access token + secret for
your own account (developer portal > your app > Keys and tokens > Access Token and Secret, Read and Write).
Requests are signed with OAuth 1.0a (HMAC-SHA1). Video: chunked v2 upload (initialize, append, finalize,
status), then POST /2/tweets with the media id.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as pysecrets
import time
import urllib.parse
from pathlib import Path

import requests

from .. import secrets
from .common import NeedsUser, chunks

API = "https://api.x.com/2"
KEYS = ("x_api_key", "x_api_secret", "x_access_token", "x_access_secret")
CHUNK = 4 * 1024 * 1024


def configured() -> bool:
    return all(secrets.get_secret(k) for k in KEYS)


def _q(s: str) -> str:
    return urllib.parse.quote(str(s), safe="~")


def oauth_header(method: str, url: str, params: dict | None = None, *, nonce: str | None = None,
                 ts: str | None = None, keys: dict | None = None) -> str:
    """OAuth 1.0a Authorization header. JSON and multipart bodies are not part of the signature."""
    k = keys or {n: secrets.get_secret(n) for n in KEYS}
    oauth = {"oauth_consumer_key": k["x_api_key"], "oauth_nonce": nonce or pysecrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": ts or str(int(time.time())),
             "oauth_token": k["x_access_token"], "oauth_version": "1.0"}
    allp = {**(params or {}), **oauth}
    base_params = "&".join(f"{_q(a)}={_q(b)}" for a, b in sorted((str(a), str(b)) for a, b in allp.items()))
    base = "&".join([method.upper(), _q(url.split("?")[0]), _q(base_params)])
    key = f"{_q(k['x_api_secret'])}&{_q(k['x_access_secret'])}"
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_q(a)}="{_q(b)}"' for a, b in sorted(oauth.items()))


def _check(r: requests.Response, what: str) -> dict:
    if r.status_code in (402, 403) and ("credits" in r.text.lower() or "enroll" in r.text.lower()
                                        or "payment" in r.text.lower() or r.status_code == 402):
        raise NeedsUser("Your X API plan doesn't allow posting. Add credits or a paid plan in the X developer "
                        "portal, or use the Post Kit.")
    if r.status_code == 401:
        raise NeedsUser("X rejected the keys. Check the four X keys in Settings > Connections.")
    if not r.ok:
        raise RuntimeError(f"X {what} failed: HTTP {r.status_code} {r.text[:200]}")
    return r.json() if r.content else {}


def me() -> dict:
    url = f"{API}/users/me"
    return _check(requests.get(url, headers={"Authorization": oauth_header("GET", url)}, timeout=20), "sign-in check")


def post(video: Path, text: str, post_id: int | None = None, poll_every: float = 3.0, max_wait: float = 300) -> dict:
    if not configured():
        raise NeedsUser("No X API keys. Posting to X uses the Post Kit unless you add paid X API keys.")
    size = video.stat().st_size
    url = f"{API}/media/upload/initialize"
    init = _check(requests.post(url, json={"media_type": "video/mp4", "total_bytes": size, "media_category": "tweet_video"},
                                headers={"Authorization": oauth_header("POST", url)}, timeout=30), "upload")
    mid = str(init["data"]["id"])
    for i, (_, data) in enumerate(chunks(video, CHUNK, post_id)):
        url = f"{API}/media/upload/{mid}/append"
        _check(requests.post(url, data={"segment_index": str(i)}, files={"media": ("clip.mp4", data, "video/mp4")},
                             headers={"Authorization": oauth_header("POST", url)}, timeout=300), "upload")
    url = f"{API}/media/upload/{mid}/finalize"
    fin = _check(requests.post(url, headers={"Authorization": oauth_header("POST", url)}, timeout=60), "upload")
    info, waited = (fin.get("data") or {}).get("processing_info"), 0.0
    while info and info.get("state") in ("pending", "in_progress"):
        if waited > max_wait:
            raise RuntimeError("X is still processing the video; will try again.")
        time.sleep(min(poll_every, info.get("check_after_secs", poll_every)))
        waited += poll_every
        url, params = f"{API}/media/upload", {"command": "STATUS", "media_id": mid}
        st = _check(requests.get(url, params=params, headers={"Authorization": oauth_header("GET", url, params)},
                                 timeout=30), "upload")
        info = (st.get("data") or {}).get("processing_info")
    if info and info.get("state") == "failed":
        raise RuntimeError(f"X couldn't process the video: {(info.get('error') or {}).get('message', 'failed')}")
    url = f"{API}/tweets"
    tw = _check(requests.post(url, json={"text": text[:280], "media": {"media_ids": [mid]}},
                              headers={"Authorization": oauth_header("POST", url)}, timeout=30), "post")
    tid = tw["data"]["id"]
    handle = (me().get("data") or {}).get("username", "i")
    return {"external_id": tid, "url": f"https://x.com/{handle}/status/{tid}"}
