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
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out: list[float] = []
    chunk = sample_rate * 2  # one second of int16
    assert proc.stdout
    while True:
        buf = proc.stdout.read(chunk)
        if not buf:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        out.append(20 * np.log10(rms + 1e-6))
    proc.wait()
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
    if x.size == 0 or sec <= 1:
        return x
    return np.convolve(x, np.ones(sec) / sec, mode="same")
