"""Features of one candidate moment, for the viral model.

Built from the per-second signals the detector already has (audio events, chat, transcript words,
the LLM rating) plus the event tags. Everything is 0..1 so weights are comparable and the "Why this moment?"
panel can show each feature's contribution.

Groups:
- streamer reaction (heaviest): laughter, screaming, disbelief words ("no way", "bro", "what"), a sudden jump in the
  streamer's voice, facecam motion (head back, leaning in, hands on head)
- chat: busier than this channel's normal, laugh / hype emotes, "clip it"
- event: what happened in the game (tags), and surprise = something unexpected followed within ~3 s by a reaction
- speech (LLM): funny / chaos / shock, and serious (counts against)
- context: community clips nearby, this week's trending tags for the game
- mundane: menus/loading/BRB, music only, calm driving, sad, long calm talking, silence
- interactions: event x reaction, explosion x laughter, surprise -> reaction
"""
from __future__ import annotations

import re

import numpy as np

from .events import TAGS

DISBELIEF = re.compile(r"^(no+|nah+|what+|bro+|wha+t|wtf|omg|yo+|dude|wait|how|why|bruh|holy|oh+|ayo|bro what)$", re.I)

FEATURES = [
    # reaction
    "laugh", "scream", "disbelief", "voice_jump", "face", "reaction",
    # chat
    "chat_busy", "chat_laugh", "chat_hype", "clip_calls",
    # event
    "event", "n_events", "explosion", "crash", "gunfire", "police", "fail_tag", "stunt", "npc", "surprise",
    # speech (LLM)
    "llm_fun", "llm_serious",
    # context
    "community", "trend",
    # mundane
    "menu", "music_only", "driving", "sad", "calm_talk", "silence",
    # interactions
    "event_x_reaction", "explosion_x_laugh", "surprise_x_reaction", "funny_x_chat",
]


def _seg(parts: dict, k: str, a: int, b: int) -> np.ndarray:
    v = parts.get(k)
    if v is None:
        return np.zeros(max(0, b - a), np.float32)
    v = np.asarray(v, np.float32)[a:b]
    return v if v.size else np.zeros(max(0, b - a), np.float32)


def _mx(x: np.ndarray) -> float:
    return float(x.max()) if x.size else 0.0


def _mean(x: np.ndarray) -> float:
    return float(x.mean()) if x.size else 0.0


def per_second(parts: dict, comp: dict, n: int, words: list[dict] | None = None, offset: float = 0.0
               ) -> dict[str, np.ndarray]:
    """Per-second reaction pieces that aren't already in `parts`."""
    z = np.zeros(n, np.float32)
    dis = np.zeros(n, np.float32)
    for w in words or []:
        i = int(float(w["start"]) - offset)
        if 0 <= i < n and DISBELIEF.match(re.sub(r"[^\w]", "", str(w["word"]))):
            dis[i] = 1.0
    # sudden jump in the voice: loudness rising fast while someone is talking/screaming (not music/explosions)
    lz = np.asarray(parts.get("loud_z", z), np.float32)[:n]
    lz = np.pad(lz, (0, n - lz.size)) if lz.size < n else lz
    rise = np.clip(np.concatenate([[0], np.diff(lz)]) / 2.5, 0, 1)
    voice = np.clip(np.asarray(parts.get("speech", z), np.float32)[:n] * 1.4 + np.asarray(parts.get("scream", z))[:n], 0, 1) \
        if "speech" in parts else np.zeros(n, np.float32)
    voice = np.pad(voice, (0, n - voice.size)) if voice.size < n else voice
    jump = rise * voice
    face = np.zeros(n, np.float32)
    if "face_motion" in parts:
        fm = np.asarray(parts["face_motion"], np.float32)[:n]
        fm = np.pad(fm, (0, n - fm.size)) if fm.size < n else fm
        mu, sd = float(fm.mean()), float(fm.std()) + 1e-6
        face = np.clip((fm - mu) / sd / 3, 0, 1)
    laugh = np.asarray(parts.get("laugh", z), np.float32)[:n]
    laugh = np.pad(laugh, (0, n - laugh.size)) if laugh.size < n else laugh
    scream = np.asarray(parts.get("scream", z), np.float32)[:n]
    scream = np.pad(scream, (0, n - scream.size)) if scream.size < n else scream
    reaction = np.clip(np.maximum.reduce([laugh * 1.2, scream, dis * 0.8, jump, face * 0.8]), 0, 1)
    # surprise: an abrupt change (motion, loudness, a cut or an event) followed within 3 s by a reaction
    motion = np.asarray(parts.get("motion", z), np.float32)[:n]
    motion = np.pad(motion, (0, n - motion.size)) if motion.size < n else motion
    mjump = np.clip(np.abs(np.concatenate([[0], np.diff(motion)])) / (motion.std() * 3 + 1e-6), 0, 1)
    ev_on = np.zeros(n, np.float32)
    for k in ("ev_explosion", "ev_crash", "ev_gunshot"):
        e = np.asarray(parts.get(k, z), np.float32)[:n]
        e = np.pad(e, (0, n - e.size)) if e.size < n else e
        ev_on = np.maximum(ev_on, np.clip(np.concatenate([[0], np.diff(e)]) * 2, 0, 1))
    novelty = np.maximum.reduce([mjump * 0.7, rise * 0.6, ev_on])
    ahead = np.array([reaction[i:i + 4].max() if i < n else 0 for i in range(n)], np.float32)
    surprise = novelty * ahead
    return {"disbelief": dis, "voice_jump": jump, "face": face, "reaction": reaction, "surprise": surprise}


def window(parts: dict, comp: dict, extra: dict, tags: dict[str, np.ndarray], a: int, b: int,
           llm: dict | None = None, trend: dict[str, float] | None = None) -> dict[str, float]:
    """Feature dict for the moment [a, b) (seconds into the source)."""
    a, b = max(0, int(a)), max(int(a) + 1, int(b))

    def s(k):
        return _seg(parts, k, a, b)

    def x(k):
        return _seg(extra, k, a, b)
    f: dict[str, float] = {}
    f["laugh"] = _mx(s("laugh"))
    f["scream"] = _mx(s("scream")) * (1 - 0.5 * min(1.0, _mx(s("sad")) * 2))
    f["disbelief"] = min(1.0, float(x("disbelief").sum()) / 3)
    f["voice_jump"] = _mx(x("voice_jump"))
    f["face"] = _mx(x("face"))
    f["reaction"] = _mx(x("reaction"))
    rate_z = s("chat_rate_z")
    f["chat_busy"] = float(np.clip((_mx(rate_z) - 0.5) / 3, 0, 1)) if rate_z.size else 0.0
    f["chat_laugh"] = _mx(s("chat_laugh")) * (f["chat_busy"] * 0.6 + 0.4) if rate_z.size else 0.0
    f["chat_hype"] = _mx(s("chat_hype")) * (f["chat_busy"] * 0.6 + 0.4) if rate_z.size else 0.0
    f["clip_calls"] = min(1.0, _mx(comp.get("chat", np.zeros(0))[a:b]) if "chat" in comp else 0.0) * \
        (1.0 if _mx(s("chat_hype")) > 0 else 0.5)
    tseg = {k: v[a:b] for k, v in tags.items()}
    tmax = {k: _mx(v) for k, v in tseg.items()}
    f["event"] = max(tmax.values()) if tmax else 0.0
    f["n_events"] = min(1.0, sum(1 for v in tmax.values() if v >= 0.45) / 3)
    for k, fk in (("explosion", "explosion"), ("crash", "crash"), ("gunfire", "gunfire"), ("police", "police"),
                  ("fail", "fail_tag"), ("stunt", "stunt"), ("npc", "npc")):
        f[fk] = tmax.get(k, 0.0)
    f["surprise"] = _mx(x("surprise"))
    if llm:
        fun = max(llm.get("funny", 0), llm.get("chaos", llm.get("hype", 0)) * 0.9, llm.get("shock", 0) * 0.8) / 10
        f["llm_fun"], f["llm_serious"] = float(fun), float(llm.get("serious", 0)) / 10
    else:
        f["llm_fun"], f["llm_serious"] = 0.0, 0.0
    f["community"] = _mx(comp["community"][a:b]) if "community" in comp else 0.0
    f["trend"] = max((tmax.get(k, 0) * w for k, w in (trend or {}).items()), default=0.0)
    # mundane
    react = f["reaction"]
    menu = np.maximum.reduce([s("static") * 0.9, s("ui"), s("dark")]) if "static" in parts else np.zeros(b - a)
    f["menu"] = _mean(menu) * (1 - react)
    f["music_only"] = _mean(np.clip(s("music") - s("speech"), 0, 1)) * (1 - react) if "music" in parts else 0.0
    f["driving"] = _mean(np.clip(s("driving") * 1.5, 0, 1)) * (1 - react) * (1 - f["event"])
    f["sad"] = float(np.clip(max(_mx(s("sad")) * 1.5, _mean(s("chat_sad")) * 1.5, f["llm_serious"] * 0.9), 0, 1)) \
        * (1 - min(1.0, f["laugh"] * 2))
    f["calm_talk"] = _mean(s("speech")) * (1 - react) * (1 - f["event"]) * (1 - f["chat_busy"])
    f["silence"] = _mean(s("silence"))
    # interactions (these are what separate "explosion + laughing streamer" from "explosion")
    f["event_x_reaction"] = f["event"] * react
    f["explosion_x_laugh"] = max(f["explosion"], f["crash"]) * max(f["laugh"], f["chat_laugh"])
    f["surprise_x_reaction"] = f["surprise"] * react
    f["funny_x_chat"] = max(f["laugh"], f["llm_fun"]) * max(f["chat_laugh"], f["chat_busy"])
    return {k: round(float(np.clip(f.get(k, 0.0), 0, 1)), 4) for k in FEATURES}


def vector(f: dict[str, float]) -> np.ndarray:
    return np.array([f.get(k, 0.0) for k in FEATURES], np.float32)


assert set(TAGS) >= {"explosion", "crash", "gunfire", "police", "fail", "stunt", "npc"}
