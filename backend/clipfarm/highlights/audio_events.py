"""Audio event detection with YAMNet (Google, Apache 2.0; AudioSet ontology CC BY-SA 4.0).

The model is the official YAMNet weights converted to ONNX by scripts/build_yamnet.py (CI), checked
against TensorFlow's own output there. It runs on onnxruntime (already installed with faster-whisper).
Features are computed here in numpy, matching YAMNet's features.py: 16 kHz mono, 25 ms Hann STFT every
10 ms, 64 mel bands 125–7500 Hz, log(mel + 0.001), 0.96 s patches every 0.48 s.

Output: per-second scores (0..1) for each event group below. Missing model -> None (the signal is
skipped and the other weights take over), so clipping still works.
"""
from __future__ import annotations

import csv
import logging
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..editing.ffmpeg_util import ffmpeg_bin

log = logging.getLogger(__name__)

SR = 16000
WIN, HOP, NFFT = 400, 160, 512
PATCH, PATCH_HOP = 96, 48

GROUPS: dict[str, list[str]] = {
    "laughter": ["Laughter", "Giggle", "Belly laugh", "Chuckle, chortle", "Snicker", "Baby laughter"],
    "scream": ["Screaming", "Shout", "Yell", "Bellow", "Whoop", "Gasp", "Children shouting", "Battle cry"],
    "cheer": ["Cheering", "Applause", "Crowd", "Clapping"],
    "explosion": ["Explosion", "Boom", "Burst, pop", "Fireworks", "Eruption", "Artillery fire"],
    "gunshot": ["Gunshot, gunfire", "Machine gun", "Fusillade", "Cap gun"],
    "crash": ["Smash, crash", "Shatter", "Breaking", "Glass", "Bang", "Thump, thud"],
    "vehicle": ["Skidding", "Tire squeal", "Police car (siren)", "Siren", "Emergency vehicle",
                "Ambulance (siren)", "Fire engine, fire truck (siren)", "Vehicle horn, car horn, honking",
                "Car passing by", "Race car, auto racing", "Revving"],
    "speech": ["Speech", "Conversation", "Narration, monologue"],
    "music": ["Music", "Background music", "Video game music", "Soundtrack music", "Theme music",
              "Electronic music", "Pop music", "Hip hop music"],
    "silence": ["Silence"],
    # music mood (used to tag music tracks)
    "m_happy": ["Happy music"], "m_funny": ["Music for children", "Ska", "Polka"], "m_sad": ["Sad music"],
    "m_tender": ["Tender music"], "m_exciting": ["Exciting music"], "m_angry": ["Angry music"],
    "m_scary": ["Scary music"], "m_vocal": ["Singing", "Vocal music", "Rapping", "Choir"],
}
REACTION = ("laughter", "scream", "cheer")
ACTION = ("explosion", "gunshot", "crash", "vehicle")


def model_dir() -> Path | None:
    from ..editing.ffmpeg_util import _ROOTS
    from .. import config
    for d in [*(r / "assets" / "models" for r in _ROOTS), config.app_data_dir() / "models"]:
        if (d / "yamnet.onnx").exists():
            return d
    return None


@lru_cache(maxsize=1)
def class_names() -> list[str]:
    d = model_dir()
    if not d:
        return []
    with open(d / "yamnet_class_map.csv", encoding="utf-8") as f:
        return [row["display_name"] for row in csv.DictReader(f)]


@lru_cache(maxsize=1)
def _session():
    d = model_dir()
    if not d:
        return None
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = 2
    return ort.InferenceSession(str(d / "yamnet.onnx"), so, providers=["CPUExecutionProvider"])


def available() -> bool:
    try:
        return _session() is not None
    except Exception as e:
        log.warning("YAMNet unavailable: %s", e)
        return False


# ---- features (numpy port of YAMNet features.py) -------------------------------------------------
def _hz_to_mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


@lru_cache(maxsize=1)
def mel_matrix() -> np.ndarray:
    """tf.signal.linear_to_mel_weight_matrix(64, 257, 16000, 125, 7500)."""
    bins = NFFT // 2 + 1
    lin = np.linspace(0.0, SR / 2, bins)[1:]
    spec_mel = _hz_to_mel(lin)[:, None]
    edges = np.linspace(_hz_to_mel(125.0), _hz_to_mel(7500.0), 64 + 2)
    lower, center, upper = edges[:-2][None], edges[1:-1][None], edges[2:][None]
    lo = (spec_mel - lower) / (center - lower)
    hi = (upper - spec_mel) / (upper - center)
    w = np.maximum(0.0, np.minimum(lo, hi))
    return np.pad(w, [[1, 0], [0, 0]]).astype(np.float32)


@lru_cache(maxsize=1)
def _hann() -> np.ndarray:
    n = np.arange(WIN)
    return (0.5 - 0.5 * np.cos(2 * np.pi * n / WIN)).astype(np.float32)   # periodic


def log_mel(wave: np.ndarray) -> np.ndarray:
    if wave.size < WIN:
        wave = np.pad(wave, (0, WIN - wave.size))
    n = 1 + (wave.size - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    frames = wave[idx] * _hann()
    mag = np.abs(np.fft.rfft(frames, n=NFFT, axis=1)).astype(np.float32)
    return np.log(mag @ mel_matrix() + 0.001).astype(np.float32)


def pad_waveform(wave: np.ndarray) -> np.ndarray:
    """YAMNet pad_waveform: at least one patch, then a whole number of 0.48 s hops."""
    min_n = int((0.96 + 0.025 - 0.010) * SR)
    hop = int(0.48 * SR)
    n = max(wave.size, min_n)
    hops = int(np.ceil((n - min_n) / hop))
    return np.pad(wave, (0, min_n + hops * hop - wave.size))


def patches(wave: np.ndarray) -> np.ndarray:
    lm = log_mel(pad_waveform(wave.astype(np.float32)))
    n = 1 + (lm.shape[0] - PATCH) // PATCH_HOP if lm.shape[0] >= PATCH else 0
    if n <= 0:
        return np.zeros((0, PATCH, 64), np.float32)
    idx = np.arange(PATCH)[None, :] + PATCH_HOP * np.arange(n)[:, None]
    return lm[idx]


def scores(wave: np.ndarray, batch: int = 64) -> np.ndarray:
    """Class probabilities per 0.48 s patch: [n_patches, 521]."""
    sess = _session()
    p = patches(wave)
    if sess is None or not len(p):
        return np.zeros((len(p), len(class_names()) or 521), np.float32)
    name = sess.get_inputs()[0].name
    out = [sess.run(None, {name: p[i:i + batch]})[0] for i in range(0, len(p), batch)]
    return np.concatenate(out, axis=0)


def group_index() -> dict[str, list[int]]:
    names = class_names()
    pos = {n: i for i, n in enumerate(names)}
    return {g: [pos[c] for c in cs if c in pos] for g, cs in GROUPS.items()}


def per_second(sc: np.ndarray, duration: int) -> dict[str, np.ndarray]:
    """Patch scores -> per-second group scores (max over the group's classes and the overlapping patches)."""
    gi = group_index()
    out = {g: np.zeros(duration, np.float32) for g in gi}
    if not len(sc):
        return out
    for g, idx in gi.items():
        if not idx:
            continue
        gp = sc[:, idx].max(axis=1)
        for k, v in enumerate(gp):
            a = int(k * 0.48)
            b = min(duration, int(np.ceil(k * 0.48 + 0.96)))
            if a < duration:
                out[g][a:b] = np.maximum(out[g][a:b], v)
    return out


def decode(media: Path, start: float = 0.0, duration: float | None = None):
    """Stream 16 kHz mono float PCM from any media file."""
    cmd = [ffmpeg_bin(), "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(media)]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def analyse(media: Path, chunk_sec: int = 120) -> dict[str, np.ndarray] | None:
    """Per-second event scores for a whole file, in chunks (multi-hour VODs stay small in memory)."""
    if not available():
        return None
    proc = decode(media)
    assert proc.stdout
    parts: dict[str, list[np.ndarray]] = {}
    carry = np.zeros(0, np.float32)
    while True:
        buf = proc.stdout.read(chunk_sec * SR * 4)
        if not buf and not carry.size:
            break
        x = np.concatenate([carry, np.frombuffer(buf, np.float32)]) if buf else carry
        secs = len(x) // SR if buf else int(np.ceil(len(x) / SR))
        if secs == 0:
            break
        seg = x[:secs * SR] if buf else x
        ps = per_second(scores(seg), secs)
        for g, v in ps.items():
            parts.setdefault(g, []).append(v)
        carry = x[secs * SR:] if buf else np.zeros(0, np.float32)
        if not buf:
            break
    proc.wait()
    return {g: np.concatenate(v) for g, v in parts.items()} if parts else None
