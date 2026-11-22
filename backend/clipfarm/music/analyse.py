"""Auto-tag a music track: BPM + beat times, loudness (LUFS), energy, mood and whether it has vocals.

Moods: funny, hype, tense, epic, chill, sad. YAMNet's music-mood classes (Happy / Sad / Tender /
Exciting / Angry / Scary music, vocals) do most of the work when the model is installed; tempo and
energy fill in. You can always edit the tags in Settings › Music.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..editing.ffmpeg_util import ffmpeg_bin, probe

MOODS = ["funny", "hype", "tense", "epic", "chill", "sad"]
SR = 22050


def _pcm(path: Path, sr: int = SR, seconds: float = 180) -> np.ndarray:
    out = subprocess.run([ffmpeg_bin(), "-v", "error", "-t", str(seconds), "-i", str(path), "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32)


def onset_envelope(x: np.ndarray, sr: int = SR, hop: int = 512) -> np.ndarray:
    n = 1 + max(0, (x.size - 2048) // hop)
    if n <= 1:
        return np.zeros(1)
    idx = np.arange(2048)[None, :] + hop * np.arange(n)[:, None]
    mag = np.log1p(10 * np.abs(np.fft.rfft(x[idx] * np.hanning(2048), axis=1)))
    flux = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)
    flux = np.concatenate([[0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")
    return np.maximum(flux, 0)


def tempo_and_beats(x: np.ndarray, sr: int = SR, hop: int = 512) -> tuple[float, list[float]]:
    oe = onset_envelope(x, sr, hop)
    fps = sr / hop
    if oe.size < fps * 4:
        return 0.0, []
    ac = np.correlate(oe - oe.mean(), oe - oe.mean(), mode="full")[oe.size - 1:]
    lags = np.arange(ac.size)
    bpms = 60 * fps / np.maximum(lags, 1)
    ok = (bpms >= 70) & (bpms <= 180)
    weight = np.exp(-0.5 * (np.log2(np.maximum(bpms, 1) / 120) / 0.9) ** 2)   # prefer ~120 BPM
    lag = int(lags[ok][np.argmax((ac * weight)[ok])])
    bpm = 60 * fps / lag
    # phase: the offset whose beat grid collects the most onset energy
    phase = int(np.argmax([oe[p::lag].sum() for p in range(lag)]))
    beats = []
    for b in range(phase, oe.size, lag):           # snap each beat to the nearest onset peak (±70 ms)
        w = max(1, int(0.07 * fps))
        lo, hi = max(0, b - w), min(oe.size, b + w + 1)
        beats.append(round((lo + int(np.argmax(oe[lo:hi]))) / fps, 3))
    return round(float(bpm), 1), beats


def drop_point(x: np.ndarray, beats: list[float], sr: int = SR) -> float | None:
    """The beat where energy jumps the most (a drop after a build)."""
    if len(beats) < 8:
        return beats[0] if beats else None
    rms = [float(np.sqrt(np.mean(x[int(b * sr):int(b * sr) + sr] ** 2) + 1e-9)) for b in beats]
    jumps = [(rms[i] / (np.mean(rms[max(0, i - 4):i]) + 1e-9), beats[i]) for i in range(4, len(beats))]
    return max(jumps)[1] if jumps else beats[0]


def analyse(path: Path) -> dict:
    from ..editing.render import integrated_lufs
    from ..highlights import audio_events as A
    x = _pcm(path)
    info = probe(path)
    bpm, beats = tempo_and_beats(x)
    lufs = integrated_lufs(path)
    oe = onset_envelope(x)
    density = float((oe > oe.mean() + oe.std()).mean()) if oe.size > 1 else 0.0
    loud = 0.0 if np.isnan(lufs) else float(np.clip((lufs + 30) / 20, 0, 1))
    energy = round(float(np.clip(0.45 * loud + 0.3 * np.clip((bpm - 70) / 90, 0, 1) + 0.25 * min(1, density * 6), 0, 1)), 3)
    moods = {m: 0.0 for m in MOODS}
    vocal = None
    if A.available():
        x16 = _pcm(path, 16000, 120)
        sc = A.per_second(A.scores(x16), int(x16.size / 16000))
        g = {k: float(np.mean(v)) if v.size else 0.0 for k, v in sc.items()}
        vocal = g.get("m_vocal", 0.0)
        moods["funny"] = g.get("m_funny", 0) * 2 + g.get("m_happy", 0) * (0.8 if 95 <= bpm <= 140 else 0.3)
        moods["hype"] = g.get("m_exciting", 0) + g.get("m_angry", 0) + 0.4 * energy * (bpm >= 115)
        moods["tense"] = g.get("m_scary", 0) * 1.5 + 0.2 * (energy < 0.6) * (bpm < 115)
        moods["epic"] = g.get("m_exciting", 0) * 0.8 + 0.3 * (energy > 0.6) * (bpm < 115)
        moods["chill"] = g.get("m_tender", 0) + 0.3 * (energy < 0.4)
        moods["sad"] = g.get("m_sad", 0) * 1.5
    else:   # tempo/energy only
        moods["hype"] = energy * (bpm >= 118)
        moods["chill"] = (1 - energy) * (bpm < 100)
        moods["funny"] = 0.4 * (100 <= bpm <= 140) * (0.35 < energy < 0.75)
        moods["epic"] = 0.4 * (energy > 0.6) * (bpm < 118)
        moods["tense"] = 0.3 * (energy < 0.6) * (bpm < 110)
        moods["sad"] = 0.4 * (energy < 0.3) * (bpm < 90)
    top = max(moods.values()) or 1.0
    scores = {k: round(float(v / top), 3) for k, v in moods.items()}
    tags = [k for k, v in sorted(scores.items(), key=lambda kv: -kv[1]) if v >= 0.6][:2] or ["chill"]
    return {"bpm": bpm, "beats": beats, "drop_at": drop_point(x, beats), "loudness": None if np.isnan(lufs) else round(lufs, 1),
            "energy": energy, "moods": tags, "mood_scores": scores,
            "instrumental": None if vocal is None else int(vocal < 0.15), "duration": round(info["duration"], 2)}
