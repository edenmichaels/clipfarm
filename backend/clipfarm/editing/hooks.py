"""Hook / title / description generation from the transcript.

Uses the configured LLM; falls back to a simple template if no model is reachable, so the
pipeline never stalls on AI availability.
"""
from __future__ import annotations

import re

from ..llm.client import LLMUnavailable, complete_json

SYSTEM = """You write short-form video packaging for gaming stream clips (TikTok, YouTube Shorts, Reels).
Return JSON only: {"hook": str, "title": str, "description": str, "hashtags": [str], "hook_style": str}
Rules:
- hook: on-screen text for the first 2 seconds, max 7 words, creates curiosity, no clickbait lies.
- title: max 70 chars, accurate to what happens.
- description: 1-2 sentences.
- hashtags: 3-6, lowercase, no '#'.
- hook_style: one of question|shock|stakes|funny|quote
- Never invent events not supported by the transcript. No slurs, no personal attacks."""


def generate_package(transcript: str, streamer: str, game: str, required_tags: list[str] | None = None) -> dict:
    user = f"Streamer: {streamer}\nGame: {game}\nTranscript of the clip:\n{transcript[:4000] or '(no speech)'}"
    try:
        out = complete_json(SYSTEM, user)
        pkg = {
            "hook": _clean(out.get("hook", ""))[:60] or _fallback_hook(transcript),
            "title": _clean(out.get("title", ""))[:100] or f"{streamer} in {game}",
            "description": _clean(out.get("description", "")),
            "hashtags": [h.strip("# ").lower() for h in out.get("hashtags", []) if h.strip("# ")][:6],
            "hook_style": out.get("hook_style", "stakes"),
            "llm": True,
        }
    except (LLMUnavailable, ValueError, KeyError):
        pkg = {"hook": _fallback_hook(transcript), "title": f"{streamer} | {game} moment",
               "description": f"{streamer} playing {game}.", "hashtags": [_tag(game), _tag(streamer), "gaming"],
               "hook_style": "stakes", "llm": False}
    for t in required_tags or []:
        t = t.strip("# ").lower()
        if t and t not in pkg["hashtags"]:
            pkg["hashtags"].append(t)
    return pkg


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().strip('"')


def _tag(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _fallback_hook(transcript: str) -> str:
    """Pick the punchiest short sentence from the transcript, else a generic hook."""
    sentences = [s.strip() for s in re.split(r"[.!?]", transcript) if 2 <= len(s.split()) <= 7]
    if sentences:
        best = max(sentences, key=lambda s: transcript.count(s) + s.count("!") + len(s) / 50)
        return f'"{best}"'
    return "Wait for it..."
