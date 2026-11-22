"""Stage 3: the LLM rates a shortlisted moment's transcript (Ollama or the configured endpoint).

Returns {"funny": 0-10, "hype": 0-10, "stop_scroll": 0-10, "type": one of TYPES, "reason": str},
or None when no model answers (the other signals carry the score).
"""
from __future__ import annotations

from ..llm.client import LLMUnavailable, complete_json
from .detect import TYPES

SYSTEM = (
    "You rate moments from gaming livestreams for short-form clips (TikTok, Shorts, Reels). Given the "
    "transcript and what the sound/chat detectors heard, return JSON: "
    '{"funny": 0-10, "hype": 0-10, "stop_scroll": 0-10, "type": one of '
    + ", ".join(f'"{t}"' for t in TYPES) +
    ', "reason": "one short line"}. funny = would viewers laugh; hype = shocking, intense or exciting; '
    "stop_scroll = would a viewer stop scrolling for this. Calm chatting, menus, reading chat, loading or "
    "waiting score 0-2. Be strict: most moments are 3-6. Base it only on what's given; don't invent events.")


def rate(transcript: str, detected: str, streamer: str, game: str) -> dict | None:
    user = (f"Streamer: {streamer}\nGame: {game}\nDetected: {detected or 'nothing notable'}\n"
            f"Transcript:\n{transcript[:3000] or '(no speech)'}")
    try:
        out = complete_json(SYSTEM, user, timeout=60)
    except (LLMUnavailable, ValueError, KeyError):
        return None
    try:
        r = {k: max(0.0, min(10.0, float(out.get(k, 0)))) for k in ("funny", "hype", "stop_scroll")}
    except (TypeError, ValueError):
        return None
    r["type"] = out.get("type") if out.get("type") in TYPES else None
    r["reason"] = str(out.get("reason", ""))[:140]
    return r
