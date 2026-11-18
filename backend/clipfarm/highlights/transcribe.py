"""Speech-to-text with word timestamps via faster-whisper (local, runs on CPU or CUDA).

Returns [{"word": str, "start": float, "end": float}]. If faster-whisper isn't installed,
returns [] and the pipeline continues without captions/speech signal (logged as a warning).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

LAUGH = {"haha", "hahaha", "lol", "lmao", "bro", "what", "no", "yo", "oh", "dude", "wait"}


@lru_cache(maxsize=1)
def _model(size: str, device: str):
    from faster_whisper import WhisperModel  # type: ignore
    if device == "auto":
        try:
            import ctranslate2  # type: ignore
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    return WhisperModel(size, device=device, compute_type="float16" if device == "cuda" else "int8")


def transcribe(media: Path, size: str = "small", device: str = "auto",
               start: float = 0.0, end: float | None = None) -> list[dict]:
    try:
        model = _model(size, device)
    except ImportError:
        return []
    kwargs = {"word_timestamps": True, "vad_filter": True}
    if start or end:
        kwargs["clip_timestamps"] = [start, end] if end else [start]
    segments, _ = model.transcribe(str(media), **kwargs)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"word": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    return words


def speech_excitement(words: list[dict], duration_sec: int) -> np.ndarray:
    """Per-second proxy for 'quotable' speech: fast talking + exclamations + reaction words."""
    out = np.zeros(duration_sec, dtype=np.float32)
    for w in words:
        i = int(w["start"])
        if 0 <= i < duration_sec:
            token = w["word"].lower().strip(".,!?")
            out[i] += 1 + (1.5 if token in LAUGH else 0) + (1 if w["word"].endswith("!") else 0)
    return out
