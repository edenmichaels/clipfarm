"""Virality score 0–100 with a plain-English reason.

Parts (weights learnable per streamer/game from your ratings and real views):
  hook       first 1–3 s: something happens or gets said straight away; a strong hook line; no dead start
  pacing     dead air, speech rate, cuts and zooms per minute
  retention  build-up -> payoff structure, ending on the reaction, short enough to loop
  relevance  game / streamer / trending topics you've imported
  quality    captions readable, layout follows campaign rules, music ducked, length right for the platform
  moment     the highlight detector's score and moment type
"""
from __future__ import annotations

import re

from .. import db
from ..edit import timeline as T

# v0.8: "moment" is now the viral model's evidence-based score (what actually goes viral), so it counts most.
DEFAULT_WEIGHTS = {"hook": 0.20, "pacing": 0.15, "retention": 0.15, "relevance": 0.10, "quality": 0.10, "moment": 0.30}
STRONG_HOOK = re.compile(r"\b(no way|wait|bro|what|insane|never|how|why|watch|craziest|caught|actually|literally)\b|[!?]",
                         re.I)
TYPE_STRENGTH = {"Funny": 1.0, "Action/Explosion": 1.0, "Rage/Reaction": 0.9, "Clutch/Skill": 0.95, "Fail": 0.9,
                 "Chaos/Fail": 1.0, "Action + Reaction": 1.0, "Rage": 0.9,
                 "Wholesome": 0.7}
BEST_LEN = {"tiktok": (15, 40), "shorts": (15, 45), "reels": (12, 35), "x": (10, 45), "srt": (15, 40)}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def parts(edl: dict, trends: list[str] | None = None, platform: str | None = None) -> tuple[dict, dict]:
    """-> (scores 0..1 per part, facts used for the reason line)."""
    dur = max(0.1, T.duration(edl))
    words = T.words_out(edl)
    peak = edl["moment"].get("peak")
    peak_out = T.to_out(edl, peak) if peak is not None else None
    facts = {"duration": round(dur, 1), "peak": peak_out}
    # hook
    first = next((w["start"] for w in words), dur)
    early_words = sum(1 for w in words if w["start"] < 3.0)
    hook_text = (edl["hook"].get("text") or "").strip()
    h = 0.35 * _clip01(1 - first / 2.0)                              # someone talks right away
    h += 0.2 * _clip01(early_words / 6)
    h += 0.25 * (1 if hook_text and len(hook_text.split()) <= 9 else 0.4 if hook_text else 0)
    h += 0.1 * (1 if STRONG_HOOK.search(hook_text + " " + " ".join(w["word"] for w in words if w["start"] < 3)) else 0)
    h += 0.1 * (1 if peak_out is not None and peak_out <= 3.5 else 0)
    facts.update(dead_start=round(first, 1), hook_text=bool(hook_text))
    # pacing
    gaps = [b["start"] - a["end"] for a, b in zip(words, words[1:])]
    dead = sum(g for g in gaps if g > 0.6) + max(0.0, first - 0.6) + (max(0.0, dur - words[-1]["end"] - 1.5) if words else dur)
    dead_frac = dead / dur
    rate = len(words) / dur
    cuts = max(0, len(T.kept(edl)) - 1) + len(edl.get("zooms", [])) + max(0, len(edl["layout"].get("keyframes", [])) - 1)
    p = 0.5 * _clip01(1 - dead_frac / 0.35) + 0.3 * _clip01(1 - abs(rate - 2.8) / 2.0) + 0.2 * _clip01(cuts / (dur / 8))
    facts.update(dead_air=round(dead, 1), words_per_sec=round(rate, 1))
    # retention
    if peak_out is not None:
        pos = peak_out / dur
        r = 0.45 * _clip01(1 - abs(pos - 0.65) / 0.45)
        tail = dur - peak_out
        r += 0.35 * (1 if 1.0 <= tail <= 5.0 else 0.5 if tail <= 8 else 0.1)
    else:
        r, tail = 0.35, None
    r += 0.2 * (1 if dur <= 30 else 0.5 if dur <= 45 else 0.1)
    facts["tail"] = round(tail, 1) if tail is not None else None
    # relevance
    rel = 0.4
    text = " ".join(w["word"] for w in words).lower() + " " + hook_text.lower()
    hits = [t for t in (trends or []) if t and t.lower() in text]
    rel += 0.4 * _clip01(len(hits) / 2)
    if edl.get("_streamer_viewers"):
        rel += 0.2 * _clip01(edl["_streamer_viewers"] / 30000)
    facts["trend_hits"] = hits[:3]
    # quality
    from ..edit.reframe import enforce_rules
    q = 0.0
    tmpl = edl["captions"].get("template")
    q += 0.3 if words and edl["captions"].get("enabled", True) else 0.0
    q += 0.25 if not enforce_rules(edl["layout"].get("keyframes", []), edl["rules"].get("onscreen") or [],
                                   edl["layout"].get("facecam"))[1] else 0.0
    q += 0.15 if (edl["music"].get("enabled") and edl["music"].get("duck_db", 0) >= 12) or not edl["music"].get("enabled") else 0.0
    lo, hi = BEST_LEN.get(platform or edl["export"].get("preset") or "tiktok", (15, 40))
    q += 0.3 * (1 if lo <= dur <= hi else _clip01(1 - (dur - hi) / 30) if dur > hi else _clip01(dur / lo))
    facts.update(template=tmpl, best_len=(lo, hi))
    # moment
    ms = edl["moment"].get("score")
    m = (0.5 + 0.5 * _clip01(float(ms))) * TYPE_STRENGTH.get(edl["moment"].get("type") or "", 0.7) if ms is not None else \
        0.5 * TYPE_STRENGTH.get(edl["moment"].get("type") or "", 0.7)
    return {"hook": _clip01(h), "pacing": _clip01(p), "retention": _clip01(r), "relevance": _clip01(rel),
            "quality": _clip01(q), "moment": _clip01(m)}, facts


def reason(sc: dict, facts: dict) -> str:
    good, fix = [], []
    if sc["hook"] >= 0.7:
        good.append("strong hook")
    elif facts["dead_start"] > 1.0:
        fix.append(f"slow start: trim {facts['dead_start']:.0f} s")
    elif not facts["hook_text"]:
        fix.append("add a hook line")
    if facts.get("peak") is not None:
        m, s = divmod(int(round(facts["peak"])), 60)
        good.append(f"payoff at {m}:{s:02d}")
    lo, hi = facts["best_len"]
    if facts["duration"] > hi + 2:
        fix.append(f"a bit long: trim {facts['duration'] - hi:.0f} s")
    elif facts["duration"] < lo - 2:
        fix.append("short: add some build-up")
    if facts["dead_air"] > 2:
        fix.append(f"{facts['dead_air']:.0f} s of dead air: remove silences")
    if facts.get("tail") is not None and facts["tail"] > 6:
        fix.append(f"ends {facts['tail']:.0f} s after the payoff: cut the ending")
    if facts.get("trend_hits"):
        good.append("on-trend: " + ", ".join(facts["trend_hits"]))
    if sc["quality"] < 0.6:
        fix.append("check captions/layout")
    text = ", ".join(good[:2]) + (", " if good and fix else "") + ", ".join(fix[:2])
    return (text[0].upper() + text[1:]) if text else "Average clip"


def score(edl: dict, weights: dict | None = None, trends: list[str] | None = None, platform: str | None = None) -> dict:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    sc, facts = parts(edl, trends, platform)
    tot = sum(w.values()) or 1
    v = round(100 * sum(w[k] * sc[k] for k in sc) / tot)
    return {"score": int(v), "parts": {k: round(x * 100) for k, x in sc.items()}, "reason": reason(sc, facts),
            "facts": facts}


def trends_list() -> list[str]:
    return [r["topic"] for r in db.q("SELECT topic FROM trend_topics WHERE active=1")] if db.q1(
        "SELECT name FROM sqlite_master WHERE name='trend_topics'") else []


def weights_for(streamer_id: int | None, game_id: int | None) -> dict:
    for scope in (f"streamer:{streamer_id}", f"game:{game_id}"):
        w = db.get_setting(f"virality_weights:{scope}")
        if w:
            return w["weights"]
    return {**DEFAULT_WEIGHTS, **(db.get_setting("virality_weights") or {})}
