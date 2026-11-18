"""Posting adapters registry + rewards-program submission checklist.

Status (see docs/ARCHITECTURE.md, milestone 2):
  youtube    implemented (posting/youtube.py)
  tiktok     Content Posting API — needs an approved TikTok developer app; unaudited apps can
             only post as private (SELF_ONLY). Adapter stub below.
  instagram  Instagram Graph API (Reels) — needs a Business/Creator account linked to a FB Page,
             and the video must be at a public URL (Graph API pulls it). Stub below.
  x          X API v2 media upload — paid API tier required for posting at volume. Stub below.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import db


class NotYetSupported(NotImplementedError):
    permanent = True


def post(platform: str, account_id: int, clip: dict) -> dict:
    tags = json.loads(clip["hashtags"] or "[]")
    if platform == "youtube":
        from .youtube import upload
        return upload(account_id, Path(clip["file_path"]), clip["title"], clip["description"], tags)
    raise NotYetSupported(f"Auto-posting to {platform} is planned for milestone 2. "
                          "Use Export and post manually for now.")


def submission_checklist(clip_id: int) -> list[dict]:
    """For programs without an API: what the user must do to get paid for this clip."""
    c = db.q1("""SELECT c.*, p.name prog, p.url prog_url, p.min_views, p.rules, p.required_tags, p.disclosure,
                        p.required_credit FROM clips c LEFT JOIN programs p ON p.id=c.program_id WHERE c.id=?""",
              (clip_id,))
    if not c or not c["prog"]:
        return [{"item": "This clip isn't linked to a rewards program.", "done": False}]
    posts = db.q("SELECT a.platform, p.url FROM posts p JOIN accounts a ON a.id=p.account_id "
                 "WHERE p.clip_id=? AND p.status='posted'", (clip_id,))
    tags = json.loads(c["required_tags"] or "[]")
    have = set(json.loads(c["hashtags"] or "[]"))
    return [
        {"item": f"Post is live on an allowed platform ({', '.join(p['platform'] for p in posts) or 'not yet'})",
         "done": bool(posts)},
        {"item": f"Required tags present: {', '.join(tags) or 'none required'}",
         "done": all(t.strip('#').lower() in have for t in tags)},
        {"item": f"Credit shown: {c['required_credit'] or 'streamer channel link'}", "done": True},
        {"item": f"Disclosure included: {c['disclosure'] or 'none required'}",
         "done": not c["disclosure"] or (c["disclosure"] in (c["description"] or ""))},
        {"item": f"Reach minimum views ({c['min_views'] or 'n/a'}) before submitting", "done": False},
        {"item": f"Submit post URL(s) at {c['prog_url'] or 'the program dashboard'}", "done": False},
    ]
