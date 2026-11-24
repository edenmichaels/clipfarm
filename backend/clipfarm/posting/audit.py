"""Posting audit (v0.8.6): what worked in 0.8.5, what didn't and why, what's fixed, plus a live account check
that runs on your PC (sign-ins, permissions, account type, quotas). The live check never posts anything."""
from __future__ import annotations

from .. import db, secrets

TABLE = [
    {"platform": "YouTube Shorts",
     "worked": "Upload through the YouTube Data API at the scheduled time; the post link went into the "
               "submission checklist.",
     "failed": "The daily limit was hard-coded to 6 (the old 1,600-units-per-upload quota; uploads now have "
               "their own 100-a-day bucket). Nothing showed how many were left, and a quota error was retried "
               "every 5 minutes instead of waiting for the next day. The whole file went up in one request "
               "(slow uploads could time out, and a deleted clip kept uploading). The description used the "
               "clip's plain description, so the campaign disclosure and required hashtags could be missing. "
               "The 7-day 'Testing' sign-in expiry only showed after it had already failed.",
     "fixed": "Quota counted per Pacific day: 'N uploads left today', extras queued for the next day. 8 MB "
              "chunks that stop if the clip is deleted. Title gets #Shorts, description gets the campaign "
              "caption (hashtags, credit, disclosure, #Shorts), made for kids = no. Testing-mode reminder before "
              "the sign-in expires."},
    {"platform": "TikTok",
     "worked": "Nothing automatic: Post & Submit offered Copy caption and Copy video path.",
     "failed": "Auto-posting raised 'planned for milestone 2'. Direct Post from an unaudited TikTok app can only "
               "post privately, and an audit is unlikely for a clipping app.",
     "fixed": "Inbox (draft) upload through the official Content Posting API: ClipFarm uploads, you get a TikTok "
              "inbox notification, add a sound, paste the caption (copied for you) and post. 5 drafts a day "
              "respected and queued ('2 of 5 TikTok drafts used today'). Status tracked; the link fills itself "
              "in when TikTok returns it, otherwise you're asked to paste it. Direct Post kept in the code, off."},
    {"platform": "Instagram Reels",
     "worked": "Nothing automatic: copy caption / copy path only.",
     "failed": "Not implemented. The old plan needed the video at a public URL.",
     "fixed": "Account check (Business/Creator, linked Facebook Page, publish permission) with step-by-step "
              "setup if it doesn't qualify. Resumable upload straight from your PC, then publish as a Reel with "
              "caption and hashtags. Daily publishing limit checked first. Falls back to the Post Kit."},
    {"platform": "X",
     "worked": "Nothing automatic.",
     "failed": "Posting needs a paid X API plan.",
     "fixed": "With your own paid X API keys it posts directly (chunked video upload + post). Without keys: "
              "Post Kit."},
    {"platform": "Post & Submit screen",
     "worked": "Submission checklist after a post link was pasted.",
     "failed": "No per-platform status, no way to post everything ready at once, and copying a file path "
               "doesn't help you drag the video into a browser. Re-scheduling a cancelled post crashed "
               "(the database allows one post per clip and account).",
     "fixed": "One row per clip with each platform's status, 'Post all ready clips', Post Kit (clipboard, upload "
              "page, Explorer with the file selected, checklist, paste link), Send to phone (QR on your Wi-Fi) "
              "and a phone sync folder. Cancelled slots no longer block a new one."},
]


def _row(name: str, ok: bool | None, detail: str) -> dict:
    return {"check": name, "ok": ok, "detail": detail}


def check_account(a: dict) -> list[dict]:
    """Live checks for one account. Calls read-only endpoints only."""
    p, aid, out = a["platform"], a["id"], []
    tok = secrets.get_secret(f"account:{aid}", as_json=True)
    try:
        if p == "youtube":
            from . import youtube
            if not tok:
                return [_row("Signed in", False, "Not connected: Settings > Connections > Connect YouTube channel.")]
            youtube.access_token(aid)
            out.append(_row("Signed in", True, "Google sign-in works (token refreshed)."))
            q = youtube.quota()
            out.append(_row("Upload quota", q["left"] > 0, f"{q['label']} ({q['used']}/{q['limit']})."))
            rem = youtube.consent_reminder(aid)
            out.append(_row("Consent screen", rem is None, rem or "No sign-in expiry detected."))
        elif p == "tiktok":
            from . import tiktok
            if not secrets.get_secret("tiktok_client_key"):
                return [_row("Developer app", False, "Add your TikTok Client key and secret in Settings > Connections.")]
            if not tok:
                return [_row("Signed in", False, "Not connected: Settings > Connections > Connect TikTok.")]
            tiktok.access_token(aid)
            out.append(_row("Signed in", True, "TikTok sign-in works."))
            scope = tok.get("scope", "")
            out.append(_row("Upload permission", "video.upload" in scope,
                            "video.upload granted." if "video.upload" in scope else
                            "video.upload is missing: add Content Posting API to your TikTok app and reconnect."))
            d = tiktok.drafts(aid)
            out.append(_row("Drafts today", d["left"] > 0, d["label"] + "."))
        elif p == "instagram":
            from . import instagram
            e = instagram.eligibility(aid)
            out.append(_row("Account qualifies", e["ok"], "Business/Creator account linked to a Facebook Page, "
                            "with the publish permission." if e["ok"] else " ".join(e["problems"])))
            if e.get("limit"):
                out.append(_row("Publishing limit", e["limit"]["left"] > 0, e["limit"]["label"] + "."))
        elif p == "x":
            from . import x
            if not x.configured():
                return [_row("API keys", None, "No paid X API keys: X uses the Post Kit (that's fine).")]
            u = (x.me().get("data") or {}).get("username")
            out.append(_row("API keys", True, f"Keys work for @{u}."))
    except Exception as e:                         # noqa: BLE001
        out.append(_row("Check", False, str(e)[:300]))
    return out


def check_all() -> list[dict]:
    from .platforms import mode, MODE_LABEL
    res = []
    for a in db.q("SELECT * FROM accounts ORDER BY id"):
        rows = check_account(a)
        a = db.q1("SELECT * FROM accounts WHERE id=?", (a["id"],))     # instagram check updates the mode
        res.append({"account_id": a["id"], "platform": a["platform"], "handle": a["handle"],
                    "mode": MODE_LABEL[mode(a)], "checks": rows,
                    "ok": all(r["ok"] is not False for r in rows)})
    db.set_setting("posting_check", {"at": db.q1("SELECT datetime('now','localtime') t")["t"], "results": res})
    return res
