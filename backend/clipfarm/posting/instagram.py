"""Instagram Reels via the Instagram API with Facebook Login (Graph API).

Why Facebook Login and not Instagram Login: uploading straight from your PC (resumable upload to
rupload.facebook.com, no public URL needed) is only available to apps that use Facebook Login for Business.
That needs: an Instagram Business or Creator account, linked to a Facebook Page, and a sign-in that grants
instagram_basic + instagram_content_publish + pages_show_list + pages_read_engagement.

Flow: create a REELS container (upload_type=resumable) -> upload the file -> wait for status FINISHED ->
media_publish -> read the permalink. The publishing limit (content_publishing_limit) is checked first.
If the account doesn't qualify, posts fall back to the Post Kit and the setup steps are shown.
"""
from __future__ import annotations

import time
import urllib.parse
from pathlib import Path

import requests

from .. import db, secrets
from .common import NeedsUser, check_cancel

VER = "v25.0"
GRAPH = f"https://graph.facebook.com/{VER}"
SCOPES = "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement,business_management"
DESKTOP_REDIRECT = "https://www.facebook.com/connect/login_success.html"
NEEDED = ("instagram_basic", "instagram_content_publish", "pages_show_list")

SETUP_STEPS = [
    {"title": "Switch to a professional account",
     "text": "Instagram app > your profile > Menu > Settings and privacy > Account type and tools > "
             "Switch to professional account. Pick Creator (or Business).",
     "link": "https://help.instagram.com/502981923235522"},
    {"title": "Link a Facebook Page",
     "text": "Create a Facebook Page (any name, e.g. GTA6 Viral Clips), then in Instagram: Edit profile > "
             "Page > Connect, or in Meta Business Suite > Settings > Accounts > Instagram accounts > Connect.",
     "link": "https://www.facebook.com/help/1148909221857370"},
    {"title": "Make a Meta developer app",
     "text": "developers.facebook.com > My Apps > Create app > use case 'Manage messaging and content on "
             "Instagram' (Business type). Copy the App ID and App Secret into ClipFarm > Settings > Connections.",
     "link": "https://developers.facebook.com/apps/"},
    {"title": "Add yourself and go Live",
     "text": "In the app: App roles > make sure your Facebook account is an admin. Add a privacy policy URL, "
             "then switch the app to Live (posts from a Development-mode app are only visible to you).",
     "link": "https://developers.facebook.com/docs/development/release"},
    {"title": "Connect in ClipFarm",
     "text": "Settings > Connections > Connect Instagram. Approve the permissions in the browser, then copy the "
             "address of the page you land on (it starts with facebook.com/connect/login_success) and paste it "
             "into ClipFarm.", "link": ""},
]


def auth_url() -> str:
    app_id = secrets.get_secret("meta_app_id")
    if not app_id:
        raise NeedsUser("Add your Meta App ID and App Secret in Settings > Connections first.")
    return "https://www.facebook.com/" + VER + "/dialog/oauth?" + urllib.parse.urlencode({
        "client_id": app_id, "redirect_uri": DESKTOP_REDIRECT, "response_type": "token", "scope": SCOPES,
        "display": "page"})


def token_from_paste(text: str) -> str:
    """Accept the landing page address (access_token in the #fragment) or a bare token."""
    text = (text or "").strip()
    if "access_token=" in text:
        frag = text.split("#", 1)[-1] if "#" in text else text.split("?", 1)[-1]
        tok = urllib.parse.parse_qs(frag).get("access_token", [""])[0]
    else:
        tok = text
    if len(tok) < 40 or " " in tok:
        raise NeedsUser("That doesn't look like the sign-in address. Copy the whole address of the page you landed on.")
    return tok


def _get(path: str, token: str, **params) -> dict:
    r = requests.get(f"{GRAPH}/{path.lstrip('/')}", params={**params, "access_token": token}, timeout=20)
    data = r.json() if r.content else {}
    if not r.ok or "error" in data:
        err = (data.get("error") or {}).get("message") or r.text[:200]
        if r.status_code in (400, 401) and "session" in err.lower():
            raise NeedsUser("Instagram sign-in expired. Reconnect Instagram in Settings > Connections.")
        raise RuntimeError(f"Instagram: {err}")
    return data


def connect(paste: str) -> dict:
    """Store a long-lived token (60 days) and find the Instagram account. Returns the eligibility check."""
    short = token_from_paste(paste)
    tok, expires = short, time.time() + 3600
    app_id, app_secret = secrets.get_secret("meta_app_id"), secrets.get_secret("meta_app_secret")
    if app_id and app_secret:
        r = requests.get(f"{GRAPH}/oauth/access_token", timeout=20, params={
            "grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
            "fb_exchange_token": short})
        if r.ok and r.json().get("access_token"):
            tok = r.json()["access_token"]
            expires = time.time() + int(r.json().get("expires_in", 60 * 86400))
    chk = eligibility_for_token(tok)
    if not chk["ig_user_id"]:
        return chk
    from .common import connect_account
    aid = connect_account("instagram", "@" + chk["username"], chk["ig_user_id"])
    secrets.set_secret(f"account:{aid}", {"access_token": tok, "expires_at": expires, "ig_user_id": chk["ig_user_id"]})
    chk["account_id"] = aid
    save_check(aid, chk)
    return chk


def eligibility_for_token(token: str) -> dict:
    steps, ig_id, username, perms = [], None, None, set()
    try:
        perms = {p["permission"] for p in _get("me/permissions", token).get("data", []) if p.get("status") == "granted"}
    except RuntimeError as e:
        return {"ok": False, "ig_user_id": None, "username": None, "problems": [str(e)], "steps": SETUP_STEPS}
    missing = [p for p in NEEDED if p not in perms]
    if missing:
        steps.append(f"The sign-in is missing: {', '.join(missing)}. Connect again and allow every permission.")
    pages = _get("me/accounts", token, fields="name,instagram_business_account{id,username}").get("data", [])
    linked = [p for p in pages if p.get("instagram_business_account")]
    if not pages:
        steps.append("No Facebook Page found on this Facebook account. Create a Page and link your Instagram to it.")
    elif not linked:
        steps.append("Your Facebook Page isn't linked to an Instagram professional account. Switch Instagram to "
                     "Creator/Business and connect it to the Page.")
    else:
        ig_id = linked[0]["instagram_business_account"]["id"]
        username = linked[0]["instagram_business_account"].get("username")
    lim = limit_for(ig_id, token) if ig_id and not missing else None
    return {"ok": bool(ig_id) and not missing, "ig_user_id": ig_id, "username": username, "problems": steps,
            "steps": [] if ig_id and not missing else SETUP_STEPS, "limit": lim}


def _token(account_id: int) -> dict:
    tok = secrets.get_secret(f"account:{account_id}", as_json=True)
    if not tok or not tok.get("access_token"):
        raise NeedsUser("Instagram isn't connected. Settings > Connections > Connect Instagram.")
    if time.time() > tok.get("expires_at", 0):
        raise NeedsUser("Instagram sign-in expired (they last 60 days). Reconnect Instagram in Settings > Connections.")
    return tok


def save_check(account_id: int, chk: dict) -> None:
    import json
    db.ex("UPDATE accounts SET eligibility=?, posting_mode=? WHERE id=?",
          (json.dumps({k: v for k, v in chk.items() if k != "steps"}), "auto" if chk.get("ok") else "kit", account_id))


def eligibility(account_id: int) -> dict:
    try:
        tok = _token(account_id)
    except NeedsUser as e:
        return {"ok": False, "ig_user_id": None, "username": None, "problems": [str(e)], "steps": SETUP_STEPS}
    chk = eligibility_for_token(tok["access_token"])
    save_check(account_id, chk)
    return chk


def limit_for(ig_id: str, token: str) -> dict:
    d = (_get(f"{ig_id}/content_publishing_limit", token, fields="quota_usage,config").get("data") or [{}])[0]
    total = int((d.get("config") or {}).get("quota_total") or 100)
    used = int(d.get("quota_usage") or 0)
    return {"used": used, "limit": total, "left": max(0, total - used),
            "label": f"{max(0, total - used)} Instagram posts left in 24 h"}


def publish(account_id: int, video: Path, caption: str, post_id: int | None = None,
            poll_every: float = 5.0, max_wait: float = 600.0) -> dict:
    tok = _token(account_id)
    token, ig = tok["access_token"], tok["ig_user_id"]
    lim = limit_for(ig, token)
    if lim["left"] <= 0:
        raise NeedsUser(f"Instagram's publishing limit is used up ({lim['used']}/{lim['limit']} in 24 h). "
                        "Try again later or use the Post Kit.")
    r = requests.post(f"{GRAPH}/{ig}/media", timeout=30, headers={"Authorization": f"Bearer {token}"},
                      data={"media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
                            "share_to_feed": "true"})
    c = r.json() if r.content else {}
    if not r.ok or "id" not in c:
        raise RuntimeError(f"Instagram refused the Reel: {(c.get('error') or {}).get('message') or r.text[:200]}")
    container, uri = c["id"], c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{VER}/{c['id']}"
    if post_id:
        db.ex("UPDATE posts SET publish_id=? WHERE id=?", (container, post_id))
    check_cancel(post_id)
    size = video.stat().st_size
    with open(video, "rb") as f:
        up = requests.post(uri, data=f.read(), timeout=900, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
    if not up.ok:
        raise RuntimeError(f"Instagram upload failed: HTTP {up.status_code} {up.text[:200]}")
    waited = 0.0
    while True:
        st = _get(container, token, fields="status_code,status")
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram couldn't process the video: {st.get('status') or code}")
        if waited >= max_wait:
            raise RuntimeError("Instagram is still processing the video; will try again.")
        check_cancel(post_id)
        time.sleep(poll_every)
        waited += poll_every
    check_cancel(post_id)
    pub = requests.post(f"{GRAPH}/{ig}/media_publish", data={"creation_id": container}, timeout=60,
                        headers={"Authorization": f"Bearer {token}"})
    pj = pub.json() if pub.content else {}
    if not pub.ok or "id" not in pj:
        raise RuntimeError(f"Instagram didn't publish: {(pj.get('error') or {}).get('message') or pub.text[:200]}")
    if post_id:
        db.ex("UPDATE posts SET sent_at=datetime('now') WHERE id=?", (post_id,))
    link = _get(pj["id"], token, fields="permalink").get("permalink") or f"https://www.instagram.com/reel/{pj['id']}/"
    return {"external_id": pj["id"], "url": link}
