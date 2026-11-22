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


# ---- v0.7: 3-5 hook options + title/description/hashtags per platform ---------------------------------
OPTIONS_SYSTEM = """You write short-form video packaging for gaming stream clips.
Return JSON only:
{"hooks": [str, str, str, str], "tiktok": {"title": str, "hashtags": [str]},
 "youtube_shorts": {"title": str, "description": str, "hashtags": [str]},
 "instagram_reels": {"caption": str, "hashtags": [str]}, "x": {"text": str}}
Rules:
- hooks: 4 different on-screen hooks for the first 2 seconds, each max 7 words, each a different angle
  (question, shock, stakes, quote from the clip, "wait for it"). Accurate: never invent events.
- tiktok title: max 80 chars, casual. 3-5 hashtags.
- youtube_shorts title: max 90 chars, searchable (game + streamer + what happens). description 1-2 sentences.
- instagram_reels caption: 1 short sentence + an emoji at most. 3-5 hashtags.
- x text: max 200 chars, no hashtags spam (max 2 inline).
- hashtags lowercase, no '#'. No slurs, no personal attacks."""

TYPE_HOOKS = {
    "Funny": ["I can't believe he said that 😂", "This is why we watch {streamer}", "Wait for the end 😂"],
    "Action/Explosion": ["This went from 0 to 100", "Nobody expected this explosion", "Watch the timing on this"],
    "Rage/Reaction": ["He was NOT ready for this", "His reaction says it all", "This made {streamer} lose it"],
    "Clutch/Skill": ["The cleanest play you'll see today", "How did he pull this off?", "1 in a million clutch"],
    "Fail": ["This went horribly wrong", "He really thought this would work", "Instant regret"],
    "Wholesome": ["This made our day", "{streamer} is too wholesome", "Chat loved this moment"],
}
PLATFORM_TAGS = {"youtube_shorts": ["shorts"], "tiktok": ["fyp"], "instagram_reels": ["reels"], "x": []}


def _tags(xs, required: list[str], extra: list[str], limit: int = 6) -> list[str]:
    out: list[str] = []
    for t in [*(r.strip("# ").lower() for r in required), *(str(x).strip("# ").lower() for x in xs or []), *extra]:
        t = re.sub(r"[^\w]", "", t)
        if t and t not in out:
            out.append(t)
    req = [re.sub(r"[^\w]", "", r.strip("# ").lower()) for r in required]
    keep = [t for t in out if t in req]
    rest = [t for t in out if t not in req]
    return keep + rest[: max(0, limit - len(keep))]


def generate_options(transcript: str, streamer: str, game: str, moment_type: str | None = None,
                     required_tags: list[str] | None = None, disclosure: str | None = None,
                     credit: str | None = None) -> dict:
    """-> {"hooks": [3-5 strings], "texts": {platform: {title, description, hashtags}}, "llm": bool}.
    Campaign hashtags are always included (spelled as the campaign spells them) and the disclosure is added."""
    req = list(required_tags or [])
    user = (f"Streamer: {streamer}\nGame: {game}\nMoment type: {moment_type or 'unknown'}\n"
            f"Transcript of the clip:\n{transcript[:4000] or '(no speech)'}")
    hooks: list[str] = []
    data: dict = {}
    llm = False
    try:
        data = complete_json(OPTIONS_SYSTEM, user)
        hooks = [_clean(h)[:60] for h in data.get("hooks", []) if _clean(h)]
        llm = bool(hooks)
    except (LLMUnavailable, ValueError, KeyError, TypeError, AttributeError):
        data = {}
    fb = [h.format(streamer=streamer) for h in TYPE_HOOKS.get(moment_type or "", ["Wait for it...", "You have to see this"])]
    quote = _fallback_hook(transcript)
    for h in [*hooks, quote, *fb]:
        if h and h.lower() not in (x.lower() for x in hooks) and len(h.split()) <= 9:
            hooks.append(h)
    hooks = [h for h in hooks if h != "Wait for it..." or len(hooks) < 4][:5]
    gtag, stag = _tag(game), _tag(streamer)
    base_tags = [gtag, stag, "gaming"]

    def pick(plat: str, key: str, default: str) -> str:
        v = (data.get(plat) or {}).get(key) if isinstance(data.get(plat), dict) else None
        return _clean(v) if v else default

    def tags(plat: str) -> list[str]:
        got = (data.get(plat) or {}).get("hashtags") if isinstance(data.get(plat), dict) else None
        return _tags(got or base_tags, req, PLATFORM_TAGS[plat])

    tail = "\n\n".join(x for x in (f"Credit: {credit}" if credit else "", disclosure or "") if x)
    first = hooks[0].strip('"') if hooks else f"{streamer} in {game}"
    texts = {
        "tiktok": {"title": pick("tiktok", "title", first)[:80], "description": tail,
                   "hashtags": tags("tiktok")},
        "youtube_shorts": {"title": pick("youtube_shorts", "title", f"{streamer} {first} | {game}")[:90],
                           "description": "\n\n".join(x for x in (pick("youtube_shorts", "description",
                                                                         f"{streamer} playing {game}."), tail) if x),
                           "hashtags": tags("youtube_shorts")},
        "instagram_reels": {"title": pick("instagram_reels", "caption", first)[:120], "description": tail,
                            "hashtags": tags("instagram_reels")},
        "x": {"title": pick("x", "text", first)[:200], "description": tail, "hashtags": _tags([], req, [], 3)},
    }
    return {"hooks": hooks, "texts": texts, "llm": llm}
