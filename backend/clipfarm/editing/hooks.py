"""Hook / title / description generation from the transcript.

Uses the configured LLM. v0.8.7: the hook always comes from this clip's own transcript and moment and is never
one of your last 50 hooks. Without a model (Ollama off, model not pulled, bad JSON) hooks are written from the
transcript's own punchiest line instead of one fixed template, and the clip is flagged so you can check it.

Why every clip had the same hook before: when the model call failed, generate_package() fell back to
_fallback_hook(), which only accepted a 2-7 word sentence split on . ! ?. Whisper word lists often have no short
punctuated sentence (or no speech at all), so nearly every clip got the same "Wait for it..." (or the same line
from TYPE_HOOKS), and nothing checked what earlier clips used.
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


def generate_package(transcript: str, streamer: str, game: str, required_tags: list[str] | None = None,
                     words: list[dict] | None = None, moment_type: str | None = None, reason: str | None = None) -> dict:
    recent = recent_hooks()
    avoid = ("\nDon't reuse any of these recent hooks: " + "; ".join(sorted(recent)[:50])) if recent else ""
    user = (f"Streamer: {streamer}\nGame: {game}\nWhat happens: {reason or moment_type or 'unknown'}\n"
            f"Transcript of the clip:\n{transcript[:4000] or '(no speech)'}{avoid}")
    local = transcript_hooks(transcript, words, streamer, moment_type, reason)
    try:
        out = complete_json(SYSTEM, user)
        pkg = {
            "hook": pick_unique([_clean(out.get("hook", ""))[:60], *local], recent),
            "title": _clean(out.get("title", ""))[:100] or f"{streamer} in {game}",
            "description": _clean(out.get("description", "")),
            "hashtags": [h.strip("# ").lower() for h in out.get("hashtags", []) if h.strip("# ")][:6],
            "hook_style": out.get("hook_style", "stakes"),
            "llm": True,
        }
    except (LLMUnavailable, ValueError, KeyError, TypeError, AttributeError):
        hook = pick_unique(local, recent)
        pkg = {"hook": hook, "title": f"{streamer}: {hook.strip(chr(34))}"[:100],
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


# ---- clip-specific hooks without a model, and "never the same hook twice in 50 clips" -----------------------------
LLM_OFF_WARNING = ("The AI model isn't answering (is Ollama running, with the model pulled?). This hook was written "
                   "from the clip's transcript instead: check it.")
EMOTIVE = re.compile(r"\b(no way|what|bro|dude|oh my god|omg|wtf|why|how|bruh|holy|run|help|stop|nah|yo)\b|!", re.I)


def norm(h: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (h or "").lower()).strip()


def recent_hooks(n: int = 50) -> set[str]:
    from .. import db
    try:
        rows = db.q("SELECT hook_text FROM clips WHERE hook_text IS NOT NULL ORDER BY id DESC LIMIT ?", (n,))
    except Exception:                                    # noqa: BLE001
        return set()
    return {norm(r["hook_text"]) for r in rows if r["hook_text"]}


def _phrases(transcript: str, words: list[dict] | None) -> list[str]:
    """Short spoken phrases: split on punctuation and on pauses (from word timings when we have them)."""
    out: list[str] = []
    if words:
        cur: list[str] = []
        for i, w in enumerate(words):
            cur.append(w["word"].strip())
            gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 9
            if gap > 0.35 or w["word"].rstrip().endswith((".", "!", "?")) or len(cur) >= 8:
                out.append(" ".join(cur))
                cur = []
    for s_ in re.split(r"(?<=[.!?])\s+", transcript or ""):
        out.append(s_)
    clean = []
    for p_ in out:
        p_ = re.sub(r"\s+", " ", p_).strip(" ,.;:-")
        n = len(p_.split())
        if 2 <= n <= 8 and p_.lower() not in (c.lower() for c in clean):
            clean.append(p_)
    return clean


def transcript_hooks(transcript: str, words: list[dict] | None, streamer: str, moment_type: str | None,
                     reason: str | None) -> list[str]:
    """Hook candidates made from THIS clip: its punchiest lines first (quoted, then framed), then its moment."""
    ph = _phrases(transcript, words)

    def punch(p_: str) -> float:
        return len(EMOTIVE.findall(p_)) * 2 + p_.count("!") + sum(c.isupper() for c in p_) / max(1, len(p_)) \
            - abs(len(p_.split()) - 4) * 0.3
    ranked = sorted(ph, key=punch, reverse=True)
    out = [f'"{p_}"' for p_ in ranked[:4]]
    out += [f'{streamer}: "{p_}"' for p_ in ranked[:3]]
    out += [f'When {streamer} said "{p_}"' for p_ in ranked[:2] if len(p_.split()) <= 6]
    if reason:
        r = re.sub(r"\s+", " ", reason).strip().rstrip(".")
        if 2 <= len(r.split()) <= 9:
            out.append(r[0].upper() + r[1:])
    fb = [h.format(streamer=streamer) for h in TYPE_HOOKS.get(moment_type or "", [])]
    out += fb
    return [h[:60] for h in out if h]


def pick_unique(cands: list[str], recent: set[str]) -> str:
    """The first candidate not used in the last 50 clips (and not empty)."""
    for h in cands:
        if h and norm(h) and norm(h) not in recent:
            return h
    base = next((h for h in cands if h), "")
    for k in range(2, 60):                           # every candidate was used: make it unique, still clip-specific
        h = f"{base} (pt {k})" if base else f"Moment #{k}"
        if norm(h) not in recent:
            return h
    return base or "Moment"


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
    "Chaos/Fail": ["This went horribly wrong", "He really thought this would work", "Pure chaos"],
    "Action + Reaction": ["His reaction to this 😭", "Nobody expected this", "Watch his face"],
    "Rage": ["He was NOT ready for this", "This made {streamer} lose it", "Instant rage"],
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
    recent = recent_hooks()
    local = transcript_hooks(transcript, None, streamer, moment_type, None)
    merged: list[str] = []
    for h in [*hooks, *local]:
        if h and norm(h) not in recent and norm(h) not in (norm(x) for x in merged) and len(h.split()) <= 10:
            merged.append(h)
    hooks = merged[:5] or [pick_unique(local, recent)]
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
