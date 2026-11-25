"""What happened in the game, per second ("event tags").

Sources, cheapest first:
- audio events (YAMNet): explosion, gunfire, crash/smash, sirens, tyre squeal
- what the streamer and chat say ("the cops", "WASTED", "backflip", "plane", "bigfoot"...)

Each tag gets a 0..1 strength per second. Tags are what the timeline markers, the tag filters ("explosions with
laughter") and the viral model use.
"""
from __future__ import annotations

import re

import numpy as np

TAGS = ["crash", "explosion", "gunfire", "police", "death", "stunt", "fall", "npc", "vehicle_steal", "plane", "boat",
        "chase", "fail", "easter_egg", "money"]
LABEL = {"crash": "crash", "explosion": "explosion", "gunfire": "gunfire", "police": "police / wanted / arrest",
         "death": "death / wasted", "stunt": "stunt / flip / jump", "fall": "fall / ragdoll", "npc": "NPC fight / reaction",
         "vehicle_steal": "vehicle steal", "plane": "plane / heli", "boat": "boat", "chase": "chase", "fail": "fail / glitch",
         "easter_egg": "easter egg / animal / myth", "money": "money / heist"}

# words said by the streamer or chat -> tag
KEYWORDS: dict[str, list[str]] = {
    "police": ["cop", "cops", "police", "wanted", "arrest", "arrested", "stars", "sheriff", "swat", "jail", "busted"],
    "death": ["wasted", "died", "dead", "killed", "respawn"],
    "crash": ["crash", "crashed", "wreck", "wrecked", "totaled"],     # not "hit": "hit me up", "hit follow"...
    "explosion": ["explode", "exploded", "explosion", "blew", "boom", "bomb", "sticky", "rpg", "grenade"],
    "gunfire": ["shoot", "shooting", "shot", "gun", "shootout"],
    "stunt": ["flip", "backflip", "frontflip", "jump", "stunt", "ramp", "trick", "wheelie"],
    "fall": ["fell", "fall", "falling", "ragdoll", "tripped", "slipped", "faceplant"],
    "npc": ["npc", "bouncer", "punched", "fight", "fighting", "attacked", "karen", "pedestrian"],
    "vehicle_steal": ["steal", "stole", "stealing", "jack", "carjack", "yoink"],
    "plane": ["plane", "jet", "heli", "helicopter", "chopper", "airport"],
    "boat": ["boat", "jetski", "yacht", "ship"],
    "chase": ["chase", "chasing", "pursuit", "running", "escape", "escaped", "getaway"],
    "fail": ["glitch", "bugged", "fail", "broke", "stuck", "clipped", "oops"],
    "easter_egg": ["easter", "egg", "bigfoot", "ufo", "alien", "ghost", "myth", "alligator", "gator", "shark", "secret"],
    "money": ["money", "cash", "bank", "heist", "vault", "rob", "robbery", "robbing", "million", "store"],
}
_KW = {w: tag for tag, ws in KEYWORDS.items() for w in ws}
_tok = re.compile(r"[a-z]+")


def _fit(x, n):
    x = np.asarray(x, np.float32) if x is not None else np.zeros(0, np.float32)
    return x[:n] if x.size >= n else np.pad(x, (0, n - x.size))


def from_signals(n: int, parts: dict, words: list[dict] | None = None, chat: list[tuple[float, str]] | None = None,
                 offset: float = 0.0) -> dict[str, np.ndarray]:
    """Per-second tag strengths from audio events, transcript words and chat messages (no video)."""
    tags = {t: np.zeros(n, np.float32) for t in TAGS}
    z = np.zeros(n, np.float32)

    def p(k):
        return _fit(parts.get(k, z), n)
    # audio events (YAMNet groups via detect.components parts)
    tags["explosion"] = np.maximum(tags["explosion"], np.clip(p("ev_explosion") * 1.3, 0, 1))
    tags["gunfire"] = np.maximum(tags["gunfire"], np.clip(p("ev_gunshot") * 1.3, 0, 1))
    tags["crash"] = np.maximum(tags["crash"], np.clip(p("ev_crash") * 1.3, 0, 1))
    tags["police"] = np.maximum(tags["police"], np.clip(p("ev_siren") * 1.1, 0, 1))
    tags["chase"] = np.maximum(tags["chase"], np.clip(np.minimum(p("ev_siren"), p("ev_vehicle")) * 2, 0, 1))
    # words: the streamer's (most reliable) and chat's
    for w in words or []:
        i = int(float(w["start"]) - offset)
        if not 0 <= i < n:
            continue
        for tok in _tok.findall(str(w["word"]).lower()):
            if tok in _KW:
                tags[_KW[tok]][i] = max(tags[_KW[tok]][i], 0.7)
    for t, msg in chat or []:
        i = int(t - offset)
        if not 0 <= i < n:
            continue
        for tok in _tok.findall(msg.lower()):
            if tok in _KW:
                tags[_KW[tok]][i] = min(1.0, tags[_KW[tok]][i] + 0.08)
    return {k: np.clip(v, 0, 1).astype(np.float32) for k, v in tags.items()}


VISION_PROMPT = (
    "These are frames from a video game livestream, a few per second. Say which of these things HAPPEN in them. "
    "Return JSON {\"tags\": [..]} using only: " + ", ".join(TAGS) + ". crash = vehicle crash; explosion; gunfire; "
    "police = police, wanted stars or an arrest; death = character dies or WASTED; stunt = flip, jump or trick; "
    "fall = character falls or ragdolls; npc = NPC fights back or reacts; vehicle_steal = stealing a vehicle; "
    "plane = plane or helicopter; boat; chase; fail = something going wrong or a glitch; easter_egg = an animal, "
    "a secret or a myth; money = money or a heist. Return an empty list if nothing happens (menus, driving, "
    "cutscenes, talking).")


def summarise(tags: dict[str, np.ndarray], a: int, b: int, min_strength: float = 0.45) -> list[dict]:
    """[{tag, t (seconds from a), strength}] for the strongest second of each tag inside [a, b)."""
    out = []
    for k, v in tags.items():
        seg = v[a:b]
        if seg.size and seg.max() >= min_strength:
            i = int(np.argmax(seg))
            out.append({"tag": k, "t": i, "strength": round(float(seg[i]), 2)})
    return sorted(out, key=lambda x: (-x["strength"], x["t"]))
