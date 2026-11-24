"""Posting: how each clip gets onto each platform, the Post Kit, and the "one posting screen" overview.

How each platform posts (v0.8.6):
  youtube    automatic: YouTube Data API upload (posting/youtube.py), daily quota shown and respected
  tiktok     TikTok inbox (draft) upload (posting/tiktok.py): you open the notification in the TikTok app,
             add a sound if you want, paste the caption, Post. Direct Post is in the code, switched off.
  instagram  automatic if the account qualifies (Business/Creator + Facebook Page + publish permission):
             resumable upload from this PC + publish (posting/instagram.py). Otherwise the Post Kit.
  x          automatic with your own paid X API keys (posting/x.py), otherwise the Post Kit
  anything that can't post automatically: the Post Kit (caption on the clipboard, upload page opened,
  video selected in Explorer, a short checklist, "Paste post link").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .. import db, proc, secrets
from .common import PLATFORM_NAME, NeedsUser, post_text


class NotYetSupported(NotImplementedError):     # kept for old callers
    permanent = True


UPLOAD_PAGES = {"youtube": "https://www.youtube.com/upload",
                "tiktok": "https://www.tiktok.com/tiktokstudio/upload",
                "instagram": "https://www.instagram.com/",
                "x": "https://x.com/compose/post"}


def _has_token(account_id: int) -> bool:
    return bool(secrets.get_secret(f"account:{account_id}", as_json=True))


def mode(acct: dict) -> str:
    """auto (posts itself) | inbox (TikTok draft) | kit (Post Kit)."""
    p = acct["platform"]
    connected = acct.get("oauth_status") in (None, "connected") and _has_token(acct["id"])
    if p == "youtube":
        return "auto" if connected else "kit"
    if p == "tiktok":
        from .tiktok import direct_enabled
        return ("auto" if direct_enabled() else "inbox") if connected else "kit"
    if p == "instagram":
        return "auto" if connected and acct.get("posting_mode") == "auto" else "kit"
    if p == "x":
        from .x import configured
        return "auto" if configured() and acct.get("posting_mode") != "kit" else "kit"
    return "kit"


MODE_LABEL = {"auto": "Posts automatically", "inbox": "Sent to your TikTok inbox", "kit": "Post Kit"}


def post(platform: str, account_id: int, clip: dict, post_id: int | None = None) -> dict:
    """Do the automatic part. Returns {"state": "posted", "url", "external_id"} or {"state": "inbox", ...}."""
    path = Path(clip["file_path"] or "")
    if not clip.get("file_path") or not path.exists():
        raise NeedsUser("The clip's video file is missing. Re-export it in the Editor.")
    t = post_text(clip, platform)
    if platform == "youtube":
        from .youtube import upload
        desc = t["caption"].replace(t["title"], "", 1).strip() if t["title"] else t["caption"]
        return {"state": "posted", **upload(account_id, path, t["title"] or clip.get("title") or "Clip", desc,
                                             t["hashtags"], post_id=post_id)}
    if platform == "tiktok":
        from . import tiktok
        if tiktok.direct_enabled():
            return {"state": "sent", **tiktok.direct_post(account_id, path, t["caption"], post_id)}
        return {"state": "inbox", **tiktok.send_to_inbox(account_id, path, post_id)}
    if platform == "instagram":
        from .instagram import publish
        return {"state": "posted", **publish(account_id, path, t["caption"], post_id)}
    if platform == "x":
        from .x import post as xpost
        text = f"{t['title']}\n\n{' '.join(t['hashtags'])}".strip()
        return {"state": "posted", **xpost(path, text, post_id)}
    raise NotYetSupported(f"No automatic posting for {platform}.")


# ---- Post Kit ------------------------------------------------------------------------------------------------
def checklist(platform: str, t: dict, trending: str | None = None) -> list[str]:
    tags = [x for x in t["required_tags"]]
    steps = {
        "tiktok": ["Drag the video in (Explorer is open with it selected)", "Paste the caption (it's on your clipboard)"],
        "youtube": ["Drag the video in (Explorer is open with it selected)", "Paste the title and description",
                    "Audience: No, it's not made for kids"],
        "instagram": ["Click Create (+) > Post, then drag the video in", "Choose Reel, then paste the caption"],
        "x": ["Click the media button and pick the video (Explorer has it selected)", "Paste the caption"],
    }.get(platform, ["Upload the video", "Paste the caption"])
    for tag in tags:
        steps.append(f"Check the campaign hashtag {tag} is in the caption")
    if t.get("disclosure"):
        steps.append(f"Check the disclosure is there: {t['disclosure']}")
    if trending and platform in ("tiktok", "instagram"):
        steps.append(f"Optional: add the trending sound \"{trending}\"")
    steps += ["Post", "Copy the post's link and paste it below"]
    return steps


def kit(post_id: int) -> dict:
    p = db.q1("""SELECT p.*, a.platform, a.handle FROM posts p JOIN accounts a ON a.id=p.account_id WHERE p.id=?""",
              (post_id,))
    if not p:
        raise KeyError(post_id)
    clip = db.q1("SELECT * FROM clips WHERE id=?", (p["clip_id"],))
    t = post_text(clip, p["platform"])
    trending = None
    if p["platform"] in ("tiktok", "instagram"):
        from ..music import trending as tm
        s = tm.suggest(clip.get("moment_type") or "Funny", p["platform"], 1)
        trending = (s[0]["name"] if s else None)
    caption = t["caption"]
    if p["platform"] == "youtube":
        caption = f"{t['title']}\n\n{caption}" if t["title"] and t["title"] not in caption else caption
    db.ex("UPDATE posts SET stage=COALESCE(stage, 'kit') WHERE id=?", (post_id,))
    return {"post_id": post_id, "platform": p["platform"], "platform_name": PLATFORM_NAME.get(p["platform"]),
            "handle": p["handle"], "caption": caption, "title": t["title"], "hashtags": t["hashtags"],
            "upload_url": UPLOAD_PAGES.get(p["platform"]), "file": clip.get("file_path"),
            "nomusic_file": clip.get("nomusic_path"), "checklist": checklist(p["platform"], t, trending),
            "trending": trending}


def reveal_command(path: str) -> list[str]:
    """The command that opens the file manager with the video selected."""
    if sys.platform.startswith("win"):
        return ["explorer", f"/select,{Path(path)}"]
    if sys.platform == "darwin":
        return ["open", "-R", path]
    return ["xdg-open", str(Path(path).parent)]


def reveal(path: str) -> list[str]:
    if not path or not Path(path).exists():
        raise NeedsUser("The video file is missing. Re-export the clip in the Editor.")
    cmd = reveal_command(path)
    try:
        proc.popen(cmd)                          # explorer returns 1 even on success: don't wait or check
    except OSError as e:
        raise NeedsUser(f"Couldn't open the file manager ({e}). The video is at {path}") from e
    return cmd


# ---- one posting screen ----------------------------------------------------------------------------------------
def _allowed(clip: dict, platform: str) -> bool:
    if not clip.get("program_id"):
        return True
    prog = db.q1("SELECT allowed_platforms FROM programs WHERE id=?", (clip["program_id"],))
    allowed = json.loads((prog or {}).get("allowed_platforms") or "[]")
    plat = {"youtube": "youtube_shorts", "instagram": "instagram_reels"}.get(platform, platform)
    return not allowed or plat in allowed


def status_of(post: dict | None, acct: dict, clip: dict) -> dict:
    if post is None:
        if not _allowed(clip, acct["platform"]):
            return {"code": "not_allowed", "label": "Campaign doesn't pay here"}
        return {"code": "waiting", "label": "Not scheduled", "mode": mode(acct)}
    if post["status"] == "posted":
        if post.get("submitted"):
            return {"code": "submitted", "label": "Submitted", "url": post["url"]}
        auto = not post["manual"] and post.get("stage") in (None, "auto")
        return {"code": "auto_posted" if auto else "posted", "label": "Auto-posted ✓" if auto else "Posted",
                "url": post["url"]}
    if post["status"] == "failed":
        return {"code": "failed", "label": "Failed", "error": post.get("error")}
    if post["status"] == "cancelled":
        return {"code": "cancelled", "label": "Cancelled"}
    if post["status"] == "uploading":
        return {"code": "uploading", "label": "Uploading…"}
    if post.get("stage") == "inbox" and post.get("sent_at"):
        return {"code": "inbox", "label": "In TikTok inbox", "note": post.get("error")}
    if post.get("stage") == "inbox_posted":
        return {"code": "inbox_posted", "label": "Posted on TikTok: paste the link"}
    if post.get("stage") == "kit" or post["manual"]:
        return {"code": "kit", "label": "Post Kit needed", "note": post.get("error")}
    return {"code": "scheduled", "label": f"{'Auto' if post.get('stage') == 'auto' else 'Inbox'} at {post['scheduled_at'][11:16]}",
            "note": post.get("error")}


def overview() -> dict:
    accts = db.q("SELECT * FROM accounts ORDER BY CASE platform WHEN 'youtube' THEN 0 WHEN 'tiktok' THEN 1 "
                 "WHEN 'instagram' THEN 2 ELSE 3 END, id")
    clips = db.q("""SELECT c.*, s.display_name streamer, pr.name program FROM clips c
                    LEFT JOIN streamers s ON s.id=c.streamer_id LEFT JOIN programs pr ON pr.id=c.program_id
                    WHERE c.deleted_at IS NULL AND c.file_path IS NOT NULL
                      AND (c.review_status='approved' OR EXISTS (SELECT 1 FROM posts p WHERE p.clip_id=c.id
                           AND p.status!='cancelled'))
                    ORDER BY COALESCE(c.virality, 0) DESC, c.id DESC LIMIT 300""")
    posts = {}
    for p in db.q("""SELECT p.*, EXISTS (SELECT 1 FROM submissions s WHERE s.post_id=p.id AND s.status='done') submitted
                     FROM posts p WHERE p.status != 'cancelled' ORDER BY p.id"""):
        posts[(p["clip_id"], p["account_id"])] = p
    rows = []
    for c in clips:
        st = []
        for a in accts:
            p = posts.get((c["id"], a["id"]))
            st.append({"account_id": a["id"], "platform": a["platform"], "handle": a["handle"],
                       "post_id": p["id"] if p else None, **status_of(p, a, c)})
        done = all(s["code"] in ("posted", "auto_posted", "submitted", "not_allowed") for s in st)
        rows.append({"clip_id": c["id"], "title": c["title"], "streamer": c["streamer"], "program": c["program"],
                     "thumb_path": c["thumb_path"], "virality": c.get("virality"), "file_path": c["file_path"],
                     "projected_aud": c.get("profit_likely"), "statuses": st, "done": done})
    return {"clips": [r for r in rows if not r["done"]], "done": [r for r in rows if r["done"]][:50],
            "accounts": [account_card(a) for a in accts]}


def account_card(a: dict) -> dict:
    m = mode(a)
    card = {"id": a["id"], "platform": a["platform"], "handle": a["handle"], "mode": m, "mode_label": MODE_LABEL[m],
            "connected": _has_token(a["id"]), "oauth_status": a.get("oauth_status")}
    try:
        if a["platform"] == "youtube":
            from .youtube import consent_reminder, quota
            card["quota"] = quota()
            if card["connected"]:
                card["reminder"] = consent_reminder(a["id"])
        elif a["platform"] == "tiktok":
            from .tiktok import drafts
            card["quota"] = drafts(a["id"])
        elif a["platform"] == "instagram" and a.get("eligibility"):
            e = json.loads(a["eligibility"])
            card["quota"] = e.get("limit")
            card["problems"] = e.get("problems")
    except Exception as e:                      # noqa: BLE001  (never break the screen over a quota read)
        card["error"] = str(e)[:200]
    return card


def post_all(now=None) -> dict:
    """Schedule every ready clip on every account it isn't on yet (spread across the day, limits respected).
    Automatic platforms upload at their slot; TikTok goes to the inbox; the rest get a Post Kit."""
    from . import submissions as subs
    ov = overview()
    made = {"auto": 0, "inbox": 0, "kit": 0}
    skipped = []
    for row in ov["clips"]:
        clip = db.q1("SELECT review_status FROM clips WHERE id=?", (row["clip_id"],))
        if clip["review_status"] != "approved":
            continue
        for s in row["statuses"]:
            if s["code"] != "waiting":
                continue
            try:
                res = subs.schedule(row["clip_id"], [s["account_id"]], now=now)
                made[res[0]["stage"]] += 1
            except subs.PostingBlocked as e:
                skipped.append(f"{row['title']} on {s['handle']}: {e}")
    total = sum(made.values())
    parts = [f"{made['auto']} will post automatically", f"{made['inbox']} go to your TikTok inbox",
             f"{made['kit']} Post Kit{'s' if made['kit'] != 1 else ''} ready"]
    return {"scheduled": total, **made, "skipped": skipped,
            "message": f"{total} post{'s' if total != 1 else ''} scheduled: " + ", ".join(parts) + "."}
