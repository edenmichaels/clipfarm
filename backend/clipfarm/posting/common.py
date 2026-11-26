"""Shared posting helpers: the post text for each platform, chunked file reading that stops when a post is
cancelled (the clip was deleted mid-upload), and the error type for "this needs you, not a retry"."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .. import db

TEXT_KEY = {"youtube": "youtube_shorts", "tiktok": "tiktok", "instagram": "instagram_reels", "x": "x"}
PLATFORM_NAME = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "x": "X"}


class NeedsUser(RuntimeError):
    """Something only the user can fix (reconnect, account setup, paid API). Not retried."""
    permanent = True


class UploadCancelled(RuntimeError):
    """The post was cancelled while uploading (usually: the clip was deleted)."""
    permanent = True


def check_cancel(post_id: int | None) -> None:
    if post_id is None:
        return
    r = db.q1("SELECT status FROM posts WHERE id=?", (post_id,))
    if not r or r["status"] == "cancelled":
        raise UploadCancelled("Upload stopped: the post was cancelled (clip deleted).")


def chunks(path: Path, size: int, post_id: int | None = None) -> Iterator[tuple[int, bytes]]:
    """(offset, bytes) pieces of a file, checking before each piece that the post wasn't cancelled."""
    with open(path, "rb") as f:
        off = 0
        while True:
            check_cancel(post_id)
            data = f.read(size)
            if not data:
                return
            yield off, data
            off += len(data)


def post_text(clip: dict, platform: str) -> dict:
    """Title, description, hashtags and the full caption for one platform. Campaign tags, credit and
    disclosure are always included (spelled exactly as the campaign spells them)."""
    from ..campaigns import rules
    edl = {}
    r = db.q1("SELECT data FROM edit_projects WHERE clip_id=?", (clip["id"],))
    if r:
        try:
            edl = json.loads(r["data"])
        except ValueError:
            edl = {}
    texts = edl.get("texts") or {}
    tx = texts.get(TEXT_KEY.get(platform, platform)) or texts.get("tiktok") or {}
    prog = db.q1("SELECT * FROM programs WHERE id=?", (clip["program_id"],)) if clip.get("program_id") else None
    er = edl.get("rules") or {}
    req_tags = er.get("tags") or json.loads((prog or {}).get("required_tags") or "[]")
    req_tags = [t if t.startswith(("#", "@")) else "#" + t for t in req_tags]
    disclosure = er.get("disclosure") or (prog or {}).get("disclosure")
    credit = er.get("credit") or (prog or {}).get("required_credit")
    title = (tx.get("title") or clip.get("title") or "").strip()
    desc = (tx.get("description") if tx else clip.get("description")) or ""
    tags = [h.lstrip("#") for h in (tx.get("hashtags") if tx else json.loads(clip.get("hashtags") or "[]")) or []]
    caption = rules.caption_text(title, desc, tags, credit, disclosure, req_tags)
    all_tags = []
    for t in req_tags + ["#" + h for h in tags]:
        if t.lower() not in (x.lower() for x in all_tags):
            all_tags.append(t)
    return {"title": title, "description": desc, "hashtags": all_tags, "caption": caption,
            "disclosure": disclosure, "credit": credit, "required_tags": req_tags,
            "program": (prog or {}).get("name")}


def connect_account(platform: str, handle: str, external_id: str) -> int:
    """Attach a sign-in to the right accounts row. Preloaded handles (no external id yet) are claimed
    instead of creating a duplicate. (The old code relied on lastrowid after an UPSERT, which can
    return a different row's id and file the token under the wrong account.)"""
    norm = handle.lstrip("@").lower()
    row = db.q1("SELECT id FROM accounts WHERE platform=? AND external_id=?", (platform, external_id)) or \
        db.q1("SELECT id FROM accounts WHERE platform=? AND external_id IS NULL AND lower(ltrim(handle,'@'))=?",
              (platform, norm))
    if row:
        db.ex("UPDATE accounts SET handle=?, external_id=?, oauth_status='connected' WHERE id=?",
              (handle, external_id, row["id"]))
        return row["id"]
    return db.ex("INSERT INTO accounts(platform, handle, external_id, oauth_status) VALUES (?,?,?,'connected')",
                 (platform, handle, external_id))
