"""Permission gate. Every job that downloads, renders or posts a streamer's content calls
`assert_can_clip` first. This is the single place the rule lives.
"""
from __future__ import annotations

from . import db

CLIPPABLE = {"allowed", "program_member"}


class NotPermitted(PermissionError):
    pass


def can_clip(streamer_id: int) -> tuple[bool, str]:
    s = db.q1("SELECT display_name, permission_status FROM streamers WHERE id=?", (streamer_id,))
    if not s:
        return False, "Unknown streamer"
    status = s["permission_status"]
    if status in CLIPPABLE:
        return True, status
    if status == "prohibited":
        return False, f"{s['display_name']} does not allow clipping."
    return False, (f"{s['display_name']}'s clipping permission is unknown. Join their rewards program "
                   "or record their permission (Streamers > Edit permission) before clipping.")


def assert_can_clip(streamer_id: int) -> None:
    ok, reason = can_clip(streamer_id)
    if not ok:
        raise NotPermitted(reason)


def program_requirements(program_id: int | None) -> dict:
    """Tags, credit and disclosure a clip must carry for its program (applied at render/post)."""
    import json
    if not program_id:
        return {"tags": [], "credit": None, "disclosure": None}
    p = db.q1("SELECT required_tags, required_credit, disclosure FROM programs WHERE id=?", (program_id,))
    if not p:
        return {"tags": [], "credit": None, "disclosure": None}
    return {"tags": json.loads(p["required_tags"] or "[]"), "credit": p["required_credit"],
            "disclosure": p["disclosure"]}
