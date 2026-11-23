"""Per-second signal extraction. Each function returns a numpy array indexed by second.

Signals:
  audio_rms        loudness/excitement from the audio track (FFmpeg -> PCM -> numpy, streamed)
  chat_rate        messages per second (live chat logged via chat_logger.py)
  emote_burst      hype emotes / caps / "!!!" per second
  viewer_delta     viewer count jumps (from snapshots during live capture)
Transcript-based "quotability" is handled in transcribe.py / scorer.py.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from ..editing.ffmpeg_util import ffmpeg_bin
from .. import proc

HYPE_TOKENS = {
    "lul", "lol", "lmao", "lmfao", "kekw", "omegalul", "pog", "pogchamp", "poggers", "pogu",
    "w", "ww", "www", "clip", "clipit", "monkas", "pepelaugh", "icant", "lulw", "xd", "holy",
    "😂", "🤣", "💀", "🔥", "gg", "ez", "noway", "insane", "omg",
}
_word = re.compile(r"[\w😂🤣💀🔥]+", re.UNICODE)


def audio_rms(media: Path, sample_rate: int = 8000) -> np.ndarray:
    """Per-second RMS loudness in dBFS, streamed so multi-hour VODs don't blow up memory."""
    cmd = [ffmpeg_bin(), "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(sample_rate),
           "-f", "s16le", "-"]
    child = proc.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out: list[float] = []
    chunk = sample_rate * 2  # one second of int16
    assert child.stdout
    while True:
        buf = child.stdout.read(chunk)
        if not buf:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        out.append(20 * np.log10(rms + 1e-6))
    child.wait()
    return np.array(out, dtype=np.float32)


def chat_series(messages: list[tuple[float, str]], duration_sec: int) -> tuple[np.ndarray, np.ndarray]:
    """messages = [(seconds_from_start, text)]. Returns (rate, emote_burst) per second."""
    rate = np.zeros(duration_sec, dtype=np.float32)
    burst = np.zeros(duration_sec, dtype=np.float32)
    for t, text in messages:
        i = int(t)
        if not 0 <= i < duration_sec:
            continue
        rate[i] += 1
        tokens = _word.findall(text.lower())
        hype = sum(1 for tok in tokens if tok in HYPE_TOKENS)
        letters = [c for c in text if c.isalpha()]
        caps = len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.7
        burst[i] += hype + (1 if caps else 0) + (1 if "!!" in text else 0)
    return rate, burst


# v0.4: reactions by kind. Tokens are matched lower-case after stripping punctuation.
LAUGH_TOKENS = {"kekw", "lul", "lulw", "omegalul", "lmao", "lmfao", "lol", "lolol", "xd", "pepelaugh", "icant",
                "kekwait", "😂", "🤣", "💀", "haha", "hahaha", "ahaha", "dead", "dying", "kek", "lulw", "omegalulw"}
HYPE_TOKENS2 = {"pog", "pogchamp", "poggers", "pogu", "w", "ww", "www", "insane", "holy", "noway", "omg", "wtf",
                "🔥", "clutch", "goated", "sheesh", "monkaw", "monkas", "pogcrazy", "ez", "gg", "letsgo", "lets"}
CLIP_TOKENS = {"clip", "clipit", "clipped", "clipthat"}
FAIL_TOKENS = {"l", "ll", "fail", "noo", "nooo", "omegalul", "classic", "bruh", "throw", "throwing", "bozo"}
# Condolences / sad chat ("F", "o7", "Sadge", "rip"): chat is reacting to something sad, which doesn't go viral.
# (These used to count as "fail" reactions, so sad moments scored as hype.)
SAD_TOKENS = {"f", "ff", "o7", "sadge", "rip", "pepehands", "biblethump", "feelsbadman", "sadcat", "🥺", "😢", "💔",
              "condolences", "prayers", "rip"}
# Chat during subs, gifted subs, raids, follows, bits and giveaways is about the event, not the gameplay.
ANNOUNCE = re.compile(r"\b(sub(s|bed|scribed|scriber|scribing)?|gift(ed|ing|s)?|raid(ed|ers?)?|follow(ed|s)?|"
                      r"prime|bits|cheer\d+|donat\w*|tier ?[123]|giveaway|hype ?train|resub)\b", re.I)
_PHRASES = {"no way": "hype", "clip it": "clip", "clip that": "clip", "lets go": "hype", "let's go": "hype"}


def chat_reactions(messages: list[tuple[float, str]], duration_sec: int) -> dict[str, np.ndarray]:
    """Per-second counts of laugh / hype / clip / fail reactions plus messages per second."""
    out = {k: np.zeros(duration_sec, dtype=np.float32) for k in ("rate", "laugh", "hype", "clip", "fail", "announce",
                                                                 "sad")}
    for t, text in messages:
        i = int(t)
        if not 0 <= i < duration_sec:
            continue
        out["rate"][i] += 1
        if ANNOUNCE.search(text):
            out["announce"][i] += 1
            continue                    # its emotes are for the sub/raid, not a reaction to the game
        low = text.lower()
        toks = _word.findall(low)
        kinds = set()
        for tok in toks:
            if tok in LAUGH_TOKENS or (tok.startswith("ha") and set(tok) <= set("ha") and len(tok) >= 4):
                kinds.add("laugh")
            if tok in HYPE_TOKENS2:
                kinds.add("hype")
            if tok in CLIP_TOKENS:
                kinds.add("clip")
            if tok in FAIL_TOKENS:
                kinds.add("fail")
            if tok in SAD_TOKENS:
                kinds.add("sad")
        for ph, kind in _PHRASES.items():
            if ph in low:
                kinds.add(kind)
        if "CLIP" in text:
            kinds.add("clip")
        for k in kinds:
            out[k][i] += 1
    return out


def viewer_delta(samples: list[tuple[float, int]], duration_sec: int) -> np.ndarray:
    """samples = [(seconds_from_start, viewers)] (sparse). Positive jumps only, held until next sample."""
    out = np.zeros(duration_sec, dtype=np.float32)
    samples = sorted(samples)
    for (t0, v0), (t1, v1) in zip(samples, samples[1:]):
        jump = max(0, v1 - v0) / max(v0, 1)
        out[int(t1):min(duration_sec, int(t1) + 60)] = jump
    return out


def zscore(x: np.ndarray, window: int = 300) -> np.ndarray:
    """Rolling z-score so a loud streamer isn't 'always exciting'. window in seconds."""
    if x.size == 0:
        return x
    k = np.ones(window) / window
    pad = window // 2
    xp = np.pad(x, (pad, pad), mode="edge")
    mean = np.convolve(xp, k, mode="same")[pad:pad + x.size]
    sq = np.convolve(xp * xp, k, mode="same")[pad:pad + x.size]
    std = np.sqrt(np.maximum(sq - mean * mean, 1e-6))
    return (x - mean) / std


def smooth(x: np.ndarray, sec: int = 3) -> np.ndarray:
    """Moving average that always returns exactly len(x) values.
    (np.convolve(mode="same") returns max(len(x), sec) values, so a source shorter than the window gave a longer
    array than every other signal, and combining them crashed with "inhomogeneous shape".)"""
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0 or sec <= 1:
        return x
    start = (sec - 1) // 2                      # same centring as mode="same" for normal-length input
    return np.convolve(x, np.ones(sec) / sec, mode="full")[start:start + x.size].astype(np.float32)
