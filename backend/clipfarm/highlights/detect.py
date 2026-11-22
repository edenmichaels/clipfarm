"""v0.4 highlight detection: funny, explosive, action-packed -- not just loud.

Score per second (weights adjustable in Settings, learned per streamer/game from your ratings):
    audio events (laughter + action sounds)  0.30
    chat reactions                           0.25
    LLM transcript rating                    0.20   (added at stage 3, for shortlisted moments only)
    visual action                            0.15
    community clips                          0.10
  - mundane penalty (menus, loading, maps, cutscenes with no reaction, BRB/AFK, calm talking, music only)

Loudness never counts on its own: it only slightly scales real audio events, and it's a rolling per-channel
z-score, so a constantly loud streamer or loud music isn't "exciting".

Every moment gets a type (Funny, Action/Explosion, Rage/Reaction, Clutch/Skill, Fail, Wholesome) and its
window starts 1–3 s before the build-up and ends once the reaction settles, with dead air trimmed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .signals import smooth, zscore

DEFAULT_WEIGHTS = {"audio_events": 0.30, "chat": 0.25, "llm": 0.20, "visual": 0.15, "community": 0.10}
PENALTY_WEIGHT = 0.45
TYPES = ["Funny", "Action/Explosion", "Rage/Reaction", "Clutch/Skill", "Fail", "Wholesome"]
FOCUS = {"balanced": (1.0, 1.0), "funny": (1.5, 0.7), "action": (0.7, 1.5)}   # (funny, action) multipliers
CHAT_DELAY = 3          # chat reacts a few seconds after the moment


def _sig(x: np.ndarray, mid: float = 1.0, k: float = 1.6) -> np.ndarray:
    return 1 / (1 + np.exp(-k * (x - mid)))


def _rel(e: np.ndarray, window: int = 300) -> np.ndarray:
    """Event probability above this channel's usual level (constant gunfire in a shooter isn't special)."""
    if e.size == 0:
        return e
    k = min(window, max(3, e.size))
    base = np.convolve(np.pad(e, (k // 2, k - k // 2 - 1), mode="edge"), np.ones(k) / k, mode="valid")
    return np.clip(e - 0.6 * base, 0, 1).astype(np.float32)


def _shift(x: np.ndarray, sec: int) -> np.ndarray:
    return np.concatenate([x[sec:], np.zeros(sec, np.float32)]) if sec and x.size > sec else x


@dataclass
class Signals:
    duration: int
    events: dict[str, np.ndarray] | None = None       # audio_events.analyse()
    loudness: np.ndarray | None = None                 # signals.audio_rms() dBFS
    chat: dict[str, np.ndarray] | None = None          # signals.chat_reactions()
    visual: dict[str, np.ndarray] | None = None        # visual.analyse() (may cover only some seconds)
    clips: list[tuple[float, int]] = field(default_factory=list)   # (offset sec, views) community clips


def _fit(x: np.ndarray | None, n: int) -> np.ndarray | None:
    if x is None:
        return None
    x = np.asarray(x, np.float32)
    return x[:n] if x.size >= n else np.pad(x, (0, n - x.size))


def components(s: Signals, focus: str = "balanced") -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """-> (scored components 0..1, raw parts used for typing / boundaries / penalty)."""
    n = s.duration
    fw, aw = FOCUS.get(focus, FOCUS["balanced"])
    comp: dict[str, np.ndarray] = {}
    parts: dict[str, np.ndarray] = {}
    loud_boost = np.ones(n, np.float32)
    if s.loudness is not None and s.loudness.size:
        lz = zscore(smooth(_fit(s.loudness, n), 3))
        loud_boost = (0.85 + 0.15 * _sig(lz, 0.5)).astype(np.float32)
        parts["loud_z"] = lz.astype(np.float32)
    if s.events:
        ev = {k: _fit(v, n) for k, v in s.events.items()}
        z = np.zeros(n, np.float32)
        laugh = _rel(ev.get("laughter", z))
        action = _rel(np.maximum.reduce([ev.get("explosion", z), ev.get("gunshot", z), ev.get("crash", z),
                                         0.7 * ev.get("vehicle", z)]))
        react = _rel(np.maximum(ev.get("scream", z), ev.get("cheer", z)))
        parts.update(laugh=laugh, action=action, scream=_rel(ev.get("scream", z)), cheer=_rel(ev.get("cheer", z)),
                     speech=ev.get("speech", z), music=ev.get("music", z), silence=ev.get("silence", z))
        a = np.clip(np.maximum.reduce([fw * laugh * 1.15, aw * action, 0.9 * react]), 0, 1)
        comp["audio_events"] = np.clip(smooth(a, 2) * loud_boost, 0, 1).astype(np.float32)
    if s.chat and s.chat.get("rate") is not None and s.chat["rate"].sum() > 0:
        ch = {k: _shift(smooth(_fit(v, n), 3), CHAT_DELAY) for k, v in s.chat.items()}
        rate_z = zscore(ch["rate"], 600)
        per_msg = (fw * ch["laugh"] + aw * ch["hype"] + 2.0 * ch["clip"] + 0.8 * ch["fail"]) / (ch["rate"] + 1.0)
        react_z = zscore(fw * ch["laugh"] + aw * ch["hype"] + 2.0 * ch["clip"] + 0.8 * ch["fail"], 600)
        c = 0.35 * _sig(rate_z, 1.0) + 0.65 * _sig(react_z, 1.0) * np.clip(per_msg * 2.5, 0, 1)
        if "announce" in ch:
            # Sub trains, raids, giveaways: if a good share of chat is about the event, chat says nothing
            # about the gameplay right now.
            share = smooth(ch["announce"], 5) / (smooth(ch["rate"], 5) + 1e-6)
            k = 12                                  # the event's chat spills over its edges
            padded = np.pad(share, (k, k), mode="edge")
            share = np.max(np.lib.stride_tricks.sliding_window_view(padded, 2 * k + 1), axis=1)[: share.size]
            c = c * (1 - np.clip((share - 0.08) * 4, 0, 1))
            parts["announce"] = share.astype(np.float32)
        comp["chat"] = np.clip(c, 0, 1).astype(np.float32)
        tot = ch["laugh"] + ch["hype"] + ch["clip"] + ch["fail"] + 1e-6
        parts.update(chat_laugh=ch["laugh"] / tot, chat_hype=ch["hype"] / tot, chat_fail=ch["fail"] / tot,
                     chat_rate_z=rate_z.astype(np.float32))
    if s.visual and s.visual.get("motion") is not None:
        vi = {k: _fit(v, n) for k, v in s.visual.items()}
        covered = vi["motion"] > 0
        mz = np.where(covered, _sig(zscore(vi["motion"], 120), 0.8), 0)
        v = 0.35 * mz + 0.25 * np.clip(vi["cuts"] * 2, 0, 1) + 0.25 * np.clip(vi["flash"] * 10, 0, 1) \
            + 0.15 * np.clip(vi["shake"] / 2.5, 0, 1)
        comp["visual"] = np.clip(smooth(v, 2), 0, 1).astype(np.float32)
        parts.update(static=vi["static"], dark=vi["dark"], ui=vi["ui"], flash=vi["flash"], motion=vi["motion"],
                     visual_covered=covered.astype(np.float32))
    if s.clips:
        cc = np.zeros(n, np.float32)
        t = np.arange(n)
        for off, views in s.clips:
            cc += np.log10(views + 10) / 4 * np.exp(-((t - off) / 20.0) ** 2)
        comp["community"] = np.clip(cc, 0, 1).astype(np.float32)
    parts["penalty"] = penalty(parts, comp, n)
    return comp, parts


def penalty(parts: dict[str, np.ndarray], comp: dict[str, np.ndarray], n: int) -> np.ndarray:
    """0..1: how mundane each second is."""
    z = np.zeros(n, np.float32)
    react = np.maximum(parts.get("laugh", z), np.maximum(parts.get("scream", z), parts.get("cheer", z)))
    action = parts.get("action", z)
    chat = comp.get("chat", z)
    excited = np.clip(np.maximum.reduce([react * 1.5, action * 1.5, chat * 1.2]), 0, 1)
    pens = []
    if "static" in parts:
        # Menus, maps, inventory, loading, BRB/AFK, character creator: a still screen nobody's reacting to.
        pens.append(np.maximum.reduce([parts["static"] * 0.9, parts["ui"], parts["dark"]]) * (1 - excited))
    if "music" in parts:
        # Music (intro/outro/ad break/loading music) with no talking and no events.
        pens.append(np.clip(parts["music"] - parts["speech"], 0, 1) * (1 - excited))
        # Long calm talking: speech, no reactions, no action, chat quiet.
        calm = parts["speech"] * (1 - excited)
        pens.append(smooth(calm, 9) * 0.7)
    if "silence" in parts:
        pens.append(parts["silence"] * 0.8)
    if not pens:
        return z
    return np.clip(smooth(np.maximum.reduce(pens), 5), 0, 1).astype(np.float32)


def curve(comp: dict[str, np.ndarray], parts: dict[str, np.ndarray], weights: dict[str, float] | None = None) -> np.ndarray:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    present = {k: v for k, v in comp.items() if k in w and k != "llm"}
    if not present:
        n = len(next(iter(parts.values()))) if parts else 0
        return np.zeros(n, np.float32)
    tot = sum(w[k] for k in present)
    c = sum(w[k] * v for k, v in present.items()) / tot
    return np.clip(c - PENALTY_WEIGHT * parts.get("penalty", 0), 0, 1).astype(np.float32)


@dataclass
class Moment:
    start: float
    end: float
    peak: float
    score: float
    type: str = "Funny"
    signals: dict = field(default_factory=dict)
    reason: str = ""


def classify(parts: dict[str, np.ndarray], a: int, b: int, llm: dict | None = None) -> str:
    if llm and llm.get("type") in TYPES:
        return llm["type"]

    def m(k):
        v = parts.get(k)
        return float(v[a:b].max()) if v is not None and b > a else 0.0
    funny = m("laugh") + 0.6 * m("chat_laugh")
    action = m("action") + 0.5 * min(1.0, m("flash") * 10)
    rage = m("scream") * (1 - min(1.0, m("laugh") * 2))
    clutch = m("cheer") + 0.6 * m("chat_hype")
    fail = 0.9 * m("chat_fail")
    scores = {"Funny": funny, "Action/Explosion": action, "Rage/Reaction": rage, "Clutch/Skill": clutch, "Fail": fail}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0.15 else "Wholesome" if m("speech") > 0.5 else "Action/Explosion"


def _quiet_point(loud: np.ndarray | None, t: int, radius: int = 1) -> int:
    if loud is None or not loud.size:
        return t
    lo, hi = max(0, t - radius), min(loud.size - 1, t + radius)
    return lo + int(np.argmin(loud[lo:hi + 1])) if hi >= lo else t


def bounds(peak: int, cv: np.ndarray, parts: dict[str, np.ndarray], loudness: np.ndarray | None,
           min_len: float, max_len: float, lead: float = 2.0) -> tuple[float, float]:
    """Start 1–3 s before the build-up; end once the reaction settles; trim dead air."""
    n = cv.size
    pv = cv[peak]
    lo = peak
    while lo > 0 and cv[lo - 1] >= 0.45 * pv and peak - lo < max_len * 0.6:
        lo -= 1
    # never start in the middle of a laugh / scream: go back to before its onset
    react = np.maximum(parts.get("laugh", np.zeros(n)), parts.get("scream", np.zeros(n)))
    while lo > 0 and react[lo - 1] > 0.3 and peak - lo < max_len * 0.7:
        lo -= 1
    start = max(0, _quiet_point(loudness, max(0, int(lo - lead)), 1))
    hi = peak
    settle = np.maximum(react, cv)
    ref = max(float(settle[peak]), 1e-6)
    while hi < n - 1 and settle[hi + 1] >= 0.3 * ref and hi - peak < max_len * 0.6:
        hi += 1
    end = min(n, _quiet_point(loudness, min(n - 1, hi + 1), 1) + 1)
    # trim dead air at the edges (silent seconds)
    if loudness is not None and loudness.size:
        while end - start > min_len and loudness[min(start, loudness.size - 1)] < -50 and start < peak - 1:
            start += 1
        while end - start > min_len and loudness[min(end - 1, loudness.size - 1)] < -50 and end > peak + 2:
            end -= 1
    length = end - start
    if length < min_len:
        extra = min_len - length
        start, end = start - extra * 0.6, end + extra * 0.4
    if end - start > max_len:
        start = max(start, peak - max_len * 0.55)
        end = start + max_len
    start, end = max(0.0, float(start)), min(float(n), float(end))
    if end - start < min_len:      # hit a file edge
        start = max(0.0, end - min_len) if end >= n else start
        end = min(float(n), start + min_len)
    return round(start, 2), round(end, 2)


def pick(cv: np.ndarray, parts: dict[str, np.ndarray], comp: dict[str, np.ndarray], loudness: np.ndarray | None,
         *, budget: int, min_len: float = 15, max_len: float = 60, min_gap: float = 30,
         threshold: float = 0.42) -> list[Moment]:
    """Peaks above threshold, best first, at least min_gap apart."""
    if cv.size == 0:
        return []
    sm = smooth(cv, 3)
    order = np.argsort(sm)[::-1]
    out: list[Moment] = []
    for idx in order:
        val = float(sm[idx])
        if val < threshold or len(out) >= budget:
            break
        if any(m.start - min_gap <= idx <= m.end + min_gap for m in out):
            continue
        a, b = bounds(int(idx), sm, parts, loudness, min_len, max_len)
        sig = {k: round(float(v[int(idx)]), 3) for k, v in comp.items()}
        sig["penalty"] = round(float(parts.get("penalty", np.zeros(1))[min(int(idx), len(parts.get("penalty", [0])) - 1)]), 3)
        mt = classify(parts, max(0, int(idx) - 4), min(cv.size, int(idx) + 5))
        out.append(Moment(a, b, float(idx), round(val, 3), mt, sig, _reason(sig, mt)))
    return out


def _reason(sig: dict, mtype: str) -> str:
    names = {"audio_events": "sound events", "chat": "chat reactions", "visual": "on-screen action",
             "community": "community clips", "llm": "AI rating"}
    top = sorted(((v, k) for k, v in sig.items() if k in names), reverse=True)[:2]
    return f"{mtype}: " + ", ".join(f"{names[k]} {v:.2f}" for v, k in top if v > 0.2) if top else mtype


def rescore_with_llm(m: Moment, rating: dict | None, weights: dict[str, float] | None = None) -> Moment:
    """Stage 3: blend the LLM's 0–10 ratings into a shortlisted moment."""
    if not rating:
        return m
    w = {**DEFAULT_WEIGHTS, **(weights or {})}["llm"]
    llm = max(rating.get("funny", 0), rating.get("hype", 0), rating.get("stop_scroll", 0)) / 10
    m.signals["llm"] = round(llm, 3)
    m.score = round((1 - w) * m.score + w * llm, 3)
    if rating.get("type") in TYPES:
        m.type = rating["type"]
    if rating.get("reason"):
        m.reason = f"{m.type}: {rating['reason']}"
    return m
