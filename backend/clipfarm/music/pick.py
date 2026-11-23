"""Pick music for a clip from its moment type and energy, and line the beat up with the edit.

  Funny            -> funny (playful / goofy), else chill
  Action/Explosion -> hype, else epic
  Rage/Reaction    -> hype, else tense
  Clutch/Skill     -> epic, else hype
  Fail             -> sad (ironic) or funny
  Wholesome        -> chill, else funny

No music at all when the campaign says "original audio only" / "no music", or the project/clip is set to
original audio only. The same track is never used twice in a row on the same account.

Alignment: for action, fails and clutches the music *drops* on the payoff (it comes in on the track's
drop, exactly at the explosion / punchline). For funny and wholesome clips it plays from the start with a
downbeat landing on the payoff. Zoom pulses go on the beats right after the payoff.
"""
from __future__ import annotations

import json
import random

from .. import db
from . import library

MOOD_FOR_TYPE = {
    "Funny": ["funny", "chill"], "Action/Explosion": ["hype", "epic"], "Rage/Reaction": ["hype", "tense"],
    "Clutch/Skill": ["epic", "hype"], "Fail": ["sad", "funny"], "Wholesome": ["chill", "funny"],
    "Chaos/Fail": ["funny", "sad"], "Action + Reaction": ["hype", "funny"], "Rage": ["hype", "tense"],
}
ENERGY_FOR_TYPE = {"Funny": 0.55, "Action/Explosion": 0.9, "Rage/Reaction": 0.8, "Clutch/Skill": 0.8,
                   "Fail": 0.35, "Wholesome": 0.35, "Chaos/Fail": 0.5, "Action + Reaction": 0.85, "Rage": 0.8}
DROP_TYPES = {"Action/Explosion", "Clutch/Skill", "Fail", "Rage/Reaction", "Chaos/Fail", "Action + Reaction", "Rage"}


def music_allowed(program: dict | None, settings: dict, clip_override: bool | None = None) -> tuple[bool, str]:
    if clip_override is True or settings.get("original_audio_only"):
        return False, "Original audio only (your choice)"
    if program and program.get("no_music"):
        return False, f"{program['name']} allows original audio only"
    if (db.get_setting("music") or {}).get("enabled") is False:
        return False, "Music is turned off in Settings"
    return True, ""


def recent_tracks(account_ids: list[int], n: int = 3) -> set[int]:
    if not account_ids:
        rows = db.q("SELECT track_id FROM music_usage ORDER BY id DESC LIMIT ?", (n,))
    else:
        q = ",".join("?" * len(account_ids))
        rows = db.q(f"SELECT track_id FROM music_usage WHERE account_id IN ({q}) ORDER BY id DESC LIMIT ?",
                    (*account_ids, n * len(account_ids)))
    return {r["track_id"] for r in rows}


def choose(moment_type: str, account_ids: list[int] | None = None, energy: float | None = None,
           seed: int | None = None) -> dict | None:
    library.ensure_bundled()
    tracks = [t for t in library.tracks(enabled_only=True) if t["exists"]]
    if not tracks:
        return None
    wants = MOOD_FOR_TYPE.get(moment_type, ["funny"])
    target = ENERGY_FOR_TYPE.get(moment_type, 0.5) if energy is None else energy
    avoid = recent_tracks(account_ids or [])
    rnd = random.Random(seed)

    def score(t):
        mood = 2.0 if wants[0] in t["moods"] else 1.0 if any(w in t["moods"] for w in wants[1:]) else 0.0
        e = 1 - abs((t["energy"] or 0.5) - target)
        fresh = -3.0 if t["id"] in avoid else 0.0
        vocal = -0.5 if t.get("instrumental") == 0 else 0.0      # vocals fight the streamer's voice
        return mood * 2 + e + fresh + vocal + rnd.random() * 0.3
    best = max(tracks, key=score)
    return best if score(best) > 0 else None


def plan(track: dict, moment_type: str, payoff: float, duration: float) -> dict:
    """-> {offset: seconds into the track at clip t=0, delay: clip seconds before the track starts,
    entry: clip time the music becomes audible, beats: clip-time beats, zoom_beats: beats from the payoff}"""
    beats = track.get("beats") or []
    drop = track.get("drop_at")
    delay = 0.0
    if moment_type in DROP_TYPES and drop is not None and payoff > 0.5:
        # the track's drop plays exactly at the payoff; music is silent before it
        offset, delay = (drop - payoff, 0.0) if drop >= payoff else (0.0, payoff - drop)
        entry = payoff
        mode = "drop on payoff"
    else:
        # a downbeat (every 4th beat) lands on the payoff, music from the start
        downs = beats[::4] or beats
        cand = [b for b in downs if b >= payoff] or [payoff]
        offset = max(0.0, cand[0] - payoff)
        entry = 0.0
        mode = "bed with downbeat on payoff"
    clip_beats = [round(b - offset + delay, 3) for b in beats if 0 <= b - offset + delay <= duration]
    after = [b for b in clip_beats if b >= payoff - 0.05][:4]
    return {"track_id": track["id"], "offset": round(offset, 3), "delay": round(delay, 3), "entry": round(entry, 3),
            "mode": mode,
            "beats": clip_beats, "zoom_beats": after, "payoff": round(payoff, 3)}


def record_use(track_id: int, clip_id: int, account_ids: list[int]) -> None:
    for a in account_ids or [None]:
        db.ex("INSERT INTO music_usage(track_id, clip_id, account_id) VALUES (?,?,?)", (track_id, clip_id, a))


def as_json(p: dict) -> str:
    return json.dumps(p)
