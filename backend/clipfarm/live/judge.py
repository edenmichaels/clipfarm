"""Tier 2: the LLM judge. Is this 60-90 s of a live stream a funny/entertaining short-form clip?

The prompt rewards funny, absurd, chaotic and shocking moments and scores sad, serious, calm or explaining
moments 0. It carries short written examples of real viral streamer moments, plus your own thumbs-up clips
(and a few thumbs-down ones) from the Editor, so it learns your taste.
"""
from __future__ import annotations

import json

from .. import db
from ..llm.client import LLMUnavailable, complete_json

EXAMPLES = [
    ("10", "Streamer swears the bridge jump is safe, car flips into the river, he sits in silence then chat floods KEKW."),
    ("9", "Mid-heist the getaway driver (a friend) drives straight into the police station; streamer screams 'WHY THERE'."),
    ("9", "Streamer's cat knocks the mic over during a tense standoff; he loses it laughing and can't speak."),
    ("9", "A random NPC steals the streamer's car seconds after he brags it's 'the safest car in the city'."),
    ("8", "Streamer tries to talk his way out of an arrest with a ridiculous fake accent and it actually works."),
    ("8", "Huge explosion out of nowhere wipes the whole crew; long stunned pause, then 'I'm uninstalling'."),
    ("8", "Streamer reads a donation message out loud that turns into a perfectly timed roast of him."),
    ("0", "Streamer calmly explains how the heist payout system works while driving to the next mission."),
    ("0", "Streamer talks seriously about a sad personal story; chat sends support messages."),
    ("0", "Quiet looting and menu browsing; streamer comments on prices."),
]

SYSTEM = """You judge moments from a live gaming stream for short-form video (TikTok, YouTube Shorts, Reels).
You get: the transcript (with seconds from the start of the cut), the chat messages in that window, and up to 3
frames. Return JSON only: {"score": 0-10 number, "reason": "one short line", "start": seconds, "end": seconds,
"type": "Funny|Chaos/Fail|Rage/Reaction|Action + Reaction|Clutch/Skill"}

Scoring:
- HIGH (7-10) only for funny, absurd, chaotic or shocking moments: a joke that lands, a ridiculous fail, a
  disaster, a huge surprise, a big genuine reaction, chat losing it (KEKW, LUL, OMEGALUL, 💀, "clip it").
- 0 for sad, serious, calm, explaining, tutorial, menu or "nothing happens" moments, even if chat is busy.
- Middle (3-6) for mildly amusing or decent action without a payoff.
- Be strict: most moments are not clips. Never reward a moment only because it is loud.
start/end: the best 15-60 s inside the cut: start a beat before the setup, end just after the reaction lands.

Examples of real viral streamer moments (score: what happened):
{examples}"""


def _mine(n: int = 4) -> list[tuple[str, str]]:
    """Your Editor ratings: top-rated clips as 9s, thumbs-down as 1s."""
    out = []
    for rating, score in ((1, "9"), (-1, "1")):
        rows = db.q("""SELECT c.title, c.hook_text, c.moment_reason, m.transcript FROM clip_feedback f
                       JOIN clips c ON c.id=f.clip_id LEFT JOIN moments m ON m.id=c.moment_id
                       WHERE f.rating=? ORDER BY f.updated_at DESC LIMIT ?""", (rating, n if rating == 1 else 2))
        for r in rows:
            what = r["moment_reason"] or r["title"] or r["hook_text"] or ""
            said = (r["transcript"] or "")[:140]
            if what or said:
                out.append((score, f"(your {'favourite' if rating == 1 else 'reject'}) {what}" +
                            (f" - said: \"{said}\"" if said else "")))
    return out


def system_prompt() -> str:
    ex = EXAMPLES + _mine()
    return SYSTEM.replace("{examples}", "\n".join(f"- {s}: {t}" for s, t in ex))


def _transcript_lines(words: list[dict]) -> str:
    lines, cur, t0 = [], [], None
    for w in words:
        if t0 is None:
            t0 = w["start"]
        cur.append(w["word"])
        if w["word"].endswith((".", "!", "?")) or len(cur) >= 14:
            lines.append(f"[{t0:5.1f}s] {' '.join(cur)}")
            cur, t0 = [], None
    if cur:
        lines.append(f"[{t0:5.1f}s] {' '.join(cur)}")
    return "\n".join(lines) or "(no speech)"


def judge(words: list[dict], chat: list[tuple[float, str]], images: list[bytes], length: float,
          streamer: str, signals: dict) -> dict:
    """-> {"score", "reason", "start", "end", "type", "llm": bool}. Without a model, a cautious score from the
    signals (chat, laughter, viewer clips) is used and marked llm=False."""
    chat_txt = "\n".join(f"[{t:5.1f}s] {m[:120]}" for t, m in chat[:150]) or "(no chat)"
    user = (f"Streamer: {streamer}\nCut length: {length:.0f} s\nSignals: {json.dumps(signals)}\n\n"
            f"Transcript:\n{_transcript_lines(words)[:5000]}\n\nChat:\n{chat_txt[:5000]}")
    try:
        out = complete_json(system_prompt(), user, timeout=120, images=images)
        score = max(0.0, min(10.0, float(out.get("score", 0))))
        start = max(0.0, min(length, float(out.get("start", 0))))
        end = max(start, min(length, float(out.get("end", length))))
        if end - start < 10:
            start, end = max(0.0, end - 30), min(length, max(end, start + 30))
        return {"score": round(score, 1), "reason": str(out.get("reason", ""))[:200], "start": round(start, 2),
                "end": round(end, 2), "type": out.get("type") or "Funny", "llm": True}
    except (LLMUnavailable, TypeError, ValueError):
        s = 10 * min(1.0, 0.45 * signals.get("chat", 0) + 0.35 * signals.get("laughter", 0)
                     + 0.4 * min(1, signals.get("viewer_clips", 0)))
        return {"score": round(min(s, 7.5), 1), "reason": "AI model not running: scored from chat, laughter and viewer clips",
                "start": max(0.0, length - 75), "end": length, "type": "Funny", "llm": False}


_ok_cache: tuple[float, bool] = (0.0, True)


def llm_ok() -> bool:
    """Is the AI model answering? (checked at most once a minute; used for the warning in the panel)."""
    import time
    import requests
    from .. import secrets
    global _ok_cache
    if time.time() - _ok_cache[0] < 60:
        return _ok_cache[1]
    cfg = db.get_setting("llm", {}) or {}
    ok = False
    try:
        if cfg.get("provider", "ollama") == "ollama":
            r = requests.get(f"{cfg.get('base_url', 'http://127.0.0.1:11434')}/api/tags", timeout=3)
            names = [m.get("name", "") for m in r.json().get("models", [])] if r.ok else []
            want = cfg.get("model", "llama3.1:8b")
            ok = any(n == want or n.split(":")[0] == want.split(":")[0] for n in names)
        else:
            ok = bool(secrets.get_secret("llm_api_key") and cfg.get("cloud_base_url"))
    except Exception:                                    # noqa: BLE001
        ok = False
    _ok_cache = (time.time(), ok)
    return ok
