"""Campaign render rules: what every clip made for a campaign must carry.

Applied automatically at render time:
  - required hashtags (e.g. #DylanPage), credit text, disclosure -> caption file + description
  - required on-screen elements -> layout choice:
      streamer_overlay  the streamer's stream overlay must stay in frame. Crop/split layouts cut the frame
                        edges where overlays live, so the full-frame "blur_fill" layout is used.
      streamer_face     the streamer must be visible. "split" keeps the facecam; otherwise full frame.
      watermark         a watermark the campaign supplies (flagged for review: ClipFarm can't add it yet)
  If a requirement can't be guaranteed in any layout, the clip is flagged for review.
"""
from __future__ import annotations

import json

from .fields import effective

FULL_FRAME = "blur_fill"


def requirements(prog: dict | None) -> dict:
    if not prog:
        return {"tags": [], "credit": None, "disclosure": None, "onscreen": [], "program_id": None,
                "program_name": None}
    tags = [t if t.startswith("#") else "#" + t for t in (effective(prog, "required_tags") or [])]
    return {"tags": tags, "credit": prog.get("required_credit"), "disclosure": prog.get("disclosure"),
            "onscreen": effective(prog, "required_onscreen") or [], "program_id": prog["id"],
            "program_name": prog["name"]}


def choose_layout(requested: str, onscreen: list[str], facecam_known: bool) -> tuple[str, list[str], list[str]]:
    """Returns (layout, notes, review_flags)."""
    notes, flags = [], []
    layout = requested or "auto"
    if "streamer_overlay" in onscreen and layout != FULL_FRAME:
        notes.append(f"Layout switched from '{layout}' to full frame so the streamer's overlay stays in frame "
                     "(campaign rule).")
        layout = FULL_FRAME
    if "streamer_face" in onscreen:
        if layout == "crop":
            layout = "split" if facecam_known else FULL_FRAME
            notes.append(f"Layout switched from action crop to '{layout}' so the streamer is visible (campaign rule).")
        elif layout == "auto" and not facecam_known:
            layout = FULL_FRAME
            notes.append("No facecam found yet, so the full frame is used to keep the streamer visible.")
        if layout == FULL_FRAME and not facecam_known:
            flags.append("Check the streamer is visible in this clip (campaign requires it).")
    if "watermark" in onscreen:
        flags.append("This campaign requires a watermark: add it before posting.")
    return layout, notes, flags


def caption_text(title: str, description: str, hashtags: list[str], credit: str | None,
                 disclosure: str | None, required_tags: list[str]) -> str:
    """The text to paste with the post. Required tags always appear, written exactly as the campaign spells them."""
    tags = []
    for t in required_tags + ["#" + h.lstrip("#") for h in hashtags]:
        if t.lower() not in (x.lower() for x in tags):
            tags.append(t)
    parts = [title.strip()]
    if description:
        parts.append(description.strip())
    if credit and credit not in (description or ""):
        parts.append(f"Credit: {credit}")
    if disclosure and disclosure not in (description or ""):
        parts.append(disclosure)
    parts.append(" ".join(tags))
    return "\n\n".join(p for p in parts if p)


def check_caption(caption: str, req: dict) -> list[str]:
    """Missing requirements (empty = compliant)."""
    missing = []
    low = caption.lower()
    for t in req["tags"]:
        if t.lower() not in low:
            missing.append(f"hashtag {t}")
    if req["disclosure"] and req["disclosure"].lower() not in low:
        missing.append(f"disclosure '{req['disclosure']}'")
    return missing


def as_json(notes: list[str], req: dict) -> str:
    return json.dumps({"program_id": req["program_id"], "program": req["program_name"], "tags": req["tags"],
                       "credit": req["credit"], "disclosure": req["disclosure"], "onscreen": req["onscreen"],
                       "notes": notes})
