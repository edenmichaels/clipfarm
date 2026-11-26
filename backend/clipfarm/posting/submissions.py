"""Posting records, posting safety and the "Submit to <campaign> on Content Rewards" checklist.

Safety rules (enforced here, not just in the UI):
  - never post the same clip (or the same rendered video, by content hash) twice to one account
  - stay under each account's posts-per-day limit and each platform's API limits
  - spread posts across the day (scheduler.plan_slots: window, minimum gap, jitter)
  - a post can only be submitted to a campaign once (submissions.post_id is UNIQUE)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .. import db
from .scheduler import plan_slots

PLATFORM_DAILY_LIMIT: dict[str, int] = {}   # API limits now live in each platform module (YouTube quota day,
                                            # TikTok 5 drafts / 24 h, Instagram content_publishing_limit)


class PostingBlocked(ValueError):
    pass


class AlreadySubmitted(ValueError):
    pass


def _clip(clip_id: int) -> dict:
    c = db.q1("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not c:
        raise PostingBlocked("Clip not found.")
    return c


def check_can_post(clip_id: int, account_id: int, when: datetime | None = None) -> None:
    c = _clip(clip_id)
    acct = db.q1("SELECT * FROM accounts WHERE id=?", (account_id,))
    if not acct:
        raise PostingBlocked("Account not found.")
    if db.q1("SELECT 1 FROM posts WHERE clip_id=? AND account_id=? AND status!='cancelled'", (clip_id, account_id)):
        raise PostingBlocked(f"This clip is already posted or scheduled on {acct['handle']}.")
    if c["content_hash"] and db.q1("""SELECT 1 FROM posts p JOIN clips c ON c.id=p.clip_id WHERE p.account_id=?
                                      AND c.content_hash=? AND p.status!='cancelled'""",
                                   (account_id, c["content_hash"])):
        raise PostingBlocked(f"The same video is already posted or scheduled on {acct['handle']}.")
    if c["program_id"]:
        prog = db.q1("SELECT name, allowed_platforms FROM programs WHERE id=?", (c["program_id"],))
        allowed = json.loads(prog["allowed_platforms"] or "[]")
        plat = {"youtube": "youtube_shorts", "instagram": "instagram_reels"}.get(acct["platform"], acct["platform"])
        if allowed and plat not in allowed:
            raise PostingBlocked(f"{prog['name']} doesn't pay for {acct['platform']} posts "
                                 f"(allowed: {', '.join(allowed)}).")
    day = (when or datetime.now()).strftime("%Y-%m-%d")
    per_day = acct["max_posts_per_day"] or db.get_setting("posting", {}).get("posts_per_day_per_account", 3)
    n = db.q1("""SELECT COUNT(*) n FROM posts WHERE account_id=? AND status!='cancelled'
                 AND substr(COALESCE(posted_at, scheduled_at),1,10)=?""", (account_id, day))["n"]
    if n >= per_day:
        raise PostingBlocked(f"{acct['handle']} already has {n} post(s) on {day} (limit {per_day}).")
    lim = PLATFORM_DAILY_LIMIT.get(acct["platform"])
    if lim:
        m = db.q1("""SELECT COUNT(*) n FROM posts p JOIN accounts a ON a.id=p.account_id WHERE a.platform=?
                     AND p.status!='cancelled' AND p.manual=0
                     AND substr(COALESCE(p.posted_at, p.scheduled_at),1,10)=?""", (acct["platform"], day))["n"]
        if m >= lim:
            raise PostingBlocked(f"{acct['platform'].title()} API limit reached for {day} ({lim} uploads).")


def schedule(clip_id: int, account_ids: list[int], now: datetime | None = None) -> list[dict]:
    """Give the clip a natural-looking slot on each account."""
    now = now or datetime.now()
    cfg = db.get_setting("posting", {})
    out = []
    for aid in account_ids:
        acct = db.q1("SELECT * FROM accounts WHERE id=?", (aid,))
        existing = [datetime.fromisoformat(r["scheduled_at"]) for r in
                    db.q("SELECT scheduled_at FROM posts WHERE account_id=? AND status IN ('scheduled','uploading','posted') "
                         "AND scheduled_at >= ?", (aid, (now - timedelta(days=1)).isoformat(sep=" ")))]
        slot = plan_slots(now, existing, 1, per_day=acct["max_posts_per_day"] or cfg.get("posts_per_day_per_account", 3),
                          start_hour=cfg.get("window_start_hour", 9), end_hour=cfg.get("window_end_hour", 22),
                          min_gap_min=cfg.get("min_gap_minutes", 90))[0]
        check_can_post(clip_id, aid, slot)
        db.ex("DELETE FROM posts WHERE clip_id=? AND account_id=? AND status='cancelled'", (clip_id, aid))
        from .platforms import mode
        stage = mode(acct)                         # auto | inbox | kit
        manual = int(stage == "kit")
        pid = db.ex("""INSERT INTO posts(clip_id, account_id, scheduled_at, status, program_id, projected_aud, manual, stage)
                       VALUES (?,?,?,'scheduled',?,?,?,?)""",
                    (clip_id, aid, slot.isoformat(sep=" "), _clip(clip_id)["program_id"],
                     _projected(clip_id, acct["platform"]), manual, stage))
        out.append({"post_id": pid, "account": acct["handle"], "platform": acct["platform"],
                    "scheduled_at": slot.isoformat(sep=" "), "manual": bool(manual), "stage": stage})
    return out


def _projected(clip_id: int, account_platform: str) -> float | None:
    c = _clip(clip_id)
    if not c["program_id"] or not c["streamer_id"]:
        return None
    plat = {"youtube": "youtube_shorts", "instagram": "instagram_reels"}.get(account_platform, account_platform)
    # v0.6: this clip's own projection (from its virality score) when we have one
    if c.get("profit_likely") is not None and c.get("profit_platform") == plat:
        return c["profit_likely"]
    r = db.q1("SELECT per_clip_likely FROM projections WHERE streamer_id=? AND program_id=? AND platform=?",
              (c["streamer_id"], c["program_id"], plat))
    if r and c.get("virality") is not None:
        from ..scoring.profit import view_multiplier
        return round(r["per_clip_likely"] * view_multiplier(int(c["virality"])), 2)
    return r["per_clip_likely"] if r else None


def record_posted(clip_id: int, account_id: int, url: str, external_id: str | None = None,
                  manual: bool = True) -> dict:
    """Mark a clip as live on an account (manual paste of the link, or an API upload) and create the
    submission checklist item."""
    url = (url or "").strip()
    if not url.startswith("http"):
        raise PostingBlocked("Paste the full link to the post (starting with https://).")
    dup = db.q1("SELECT p.id FROM posts p WHERE p.url=? AND p.status='posted'", (url,))
    post = db.q1("SELECT * FROM posts WHERE clip_id=? AND account_id=?", (clip_id, account_id))
    if dup and (not post or dup["id"] != post["id"]):
        raise PostingBlocked("That post link is already recorded for another clip.")
    if post and post["status"] == "posted":
        raise PostingBlocked("This clip is already marked as posted on that account.")
    if post and post["status"] == "cancelled":
        db.ex("DELETE FROM posts WHERE id=?", (post["id"],))     # a cancelled slot doesn't block a real post
        post = None
    if not post:
        check_can_post(clip_id, account_id)
        pid = db.ex("""INSERT INTO posts(clip_id, account_id, scheduled_at, posted_at, status, url, external_id,
                       program_id, projected_aud, manual) VALUES (?,?,datetime('now','localtime'),datetime('now','localtime'),'posted',?,?,?,?,?)""",
                    (clip_id, account_id, url, external_id, _clip(clip_id)["program_id"],
                     _projected(clip_id, db.q1("SELECT platform FROM accounts WHERE id=?", (account_id,))["platform"]),
                     int(manual)))
    else:
        pid = post["id"]
        db.ex("""UPDATE posts SET status='posted', posted_at=datetime('now','localtime'), url=?, external_id=COALESCE(?, external_id),
                 error=NULL WHERE id=?""", (url, external_id, pid))
    item = create_submission_item(pid)
    return {"post_id": pid, "submission": item}


def create_submission_item(post_id: int) -> dict | None:
    p = db.q1("""SELECT p.*, c.program_id clip_program FROM posts p JOIN clips c ON c.id=p.clip_id
                 WHERE p.id=?""", (post_id,))
    prog_id = p["program_id"] or p["clip_program"]
    if not prog_id or p["status"] != "posted":
        return None
    db.ex("""INSERT INTO submissions(post_id, program_id, post_url) VALUES (?,?,?)
             ON CONFLICT(post_id) DO NOTHING""", (post_id, prog_id, p["url"]))
    return checklist_item(db.q1("SELECT id FROM submissions WHERE post_id=?", (post_id,))["id"])


def checklist_item(sub_id: int) -> dict:
    r = db.q1("""SELECT s.*, pr.name program, pr.url program_url, pr.operator, a.handle, a.platform, c.title
                 FROM submissions s JOIN programs pr ON pr.id=s.program_id JOIN posts p ON p.id=s.post_id
                 JOIN accounts a ON a.id=p.account_id JOIN clips c ON c.id=p.clip_id WHERE s.id=?""", (sub_id,))
    site = "Content Rewards" if r["operator"] == "content_rewards" else (r["operator"] or "the program").title()
    return {**r, "label": f"Submit to {r['program']} on {site}"}


def checklist(status: str | None = "todo") -> list[dict]:
    ids = db.q("SELECT id FROM submissions" + (" WHERE status=?" if status else "") + " ORDER BY id DESC",
               (status,) if status else ())
    return [checklist_item(r["id"]) for r in ids]


def mark_submitted(sub_id: int, done: bool = True) -> dict:
    s = db.q1("SELECT * FROM submissions WHERE id=?", (sub_id,))
    if not s:
        raise KeyError(sub_id)
    if done and s["status"] == "done":
        raise AlreadySubmitted("This post was already submitted. Submitting it twice can get you banned "
                               "from the campaign.")
    db.ex("UPDATE submissions SET status=?, done_at=CASE WHEN ?='done' THEN datetime('now') END WHERE id=?",
          ("done" if done else "todo", "done" if done else "todo", sub_id))
    if done:
        p = db.q1("SELECT post_id, program_id FROM submissions WHERE id=?", (sub_id,))
        if not db.q1("SELECT 1 FROM payouts WHERE post_id=?", (p["post_id"],)):
            db.ex("""INSERT INTO payouts(program_id, post_id, amount, status, submitted_at)
                     VALUES (?,?,0,'pending',datetime('now'))""", (p["program_id"], p["post_id"]))
    return checklist_item(sub_id)
