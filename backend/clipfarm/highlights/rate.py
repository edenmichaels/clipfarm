"""Stage 3: the LLM rates a shortlisted moment's transcript (Ollama or the configured endpoint).

Returns {"funny", "chaos", "shock", "serious": 0-10, "type": one of TYPES, "reason": str} (serious = sad, serious,
emotional or calm, which counts AGAINST the moment),
or None when no model answers (the other signals carry the score).
"""
from __future__ import annotations

from ..llm.client import LLMUnavailable, complete_json
from .detect import TYPES

SYSTEM = (
    "You rate moments from gaming livestreams for short-form clips (TikTok, Shorts, Reels). What goes viral is "
    "ENTERTAINMENT: comedy and chaos driven by the streamer's reaction (laughing, screaming, disbelief), fails, "
    "absurd or unexpected things, NPCs fighting back, spectacle WITH a big reaction. Sad, serious, emotional, "
    "calm, explaining, story/RP dialogue, menus, loading, reading chat and driving around do NOT go viral. "
    "Given the transcript and what the sound/chat detectors heard, return JSON: "
    '{"funny": 0-10, "chaos": 0-10, "shock": 0-10, "serious": 0-10, "type": one of '
    + ", ".join(f'"{t}"' for t in TYPES) +
    ', "reason": "one short line"}. funny = would viewers laugh; chaos = absurd, chaotic, a fail or things going '
    "wrong; shock = genuinely surprising or unexpected; serious = sad, serious, emotional, calm or explaining "
    "(high serious means NOT entertaining). Be strict: most moments are 2-5. Base it only on what's given; "
    "don't invent events.")


def rate(transcript: str, detected: str, streamer: str, game: str) -> dict | None:
    user = (f"Streamer: {streamer}\nGame: {game}\nDetected: {detected or 'nothing notable'}\n"
            f"Transcript:\n{transcript[:3000] or '(no speech)'}")
    try:
        out = complete_json(SYSTEM, user, timeout=60)
    except (LLMUnavailable, ValueError, KeyError):
        return None
    try:
        r = {k: max(0.0, min(10.0, float(out.get(k, 0)))) for k in ("funny", "chaos", "shock", "serious")}
        r["hype"] = r["chaos"]                       # older callers read "hype"
    except (TypeError, ValueError):
        return None
    r["type"] = out.get("type") if out.get("type") in TYPES else None
    r["reason"] = str(out.get("reason", ""))[:140]
    return r
