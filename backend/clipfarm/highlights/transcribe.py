"""Speech-to-text with word timestamps via faster-whisper (local, runs on CPU or CUDA).

Returns [{"word": str, "start": float, "end": float}]. If faster-whisper isn't installed,
returns [] and the pipeline continues without captions/speech signal (logged as a warning).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import numpy as np

log = logging.getLogger("clipfarm.transcribe")
LAUGH = {"haha", "hahaha", "lol", "lmao", "bro", "what", "no", "yo", "oh", "dude", "wait"}


def models_dir() -> Path:
    """Whisper models live in %APPDATA%\\ClipFarm\\models (not the install folder), so they survive
    updates and the health check can see and re-download them."""
    from .. import config
    d = config.app_data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_present(size: str) -> bool:
    # faster-whisper stores models as models--Systran--faster-whisper-<size>/snapshots/<rev>/model.bin
    return any(models_dir().glob(f"models--*faster-whisper-{size}/snapshots/*/model.bin"))


def download_model(size: str) -> Path:
    from faster_whisper.utils import download_model as _dl  # type: ignore
    return Path(_dl(size, cache_dir=str(models_dir())))


@lru_cache(maxsize=2)
def _model(size: str, device: str):
    from faster_whisper import WhisperModel  # type: ignore
    if device == "auto":
        try:
            import ctranslate2  # type: ignore
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    try:
        return WhisperModel(size, device=device, compute_type="float16" if device == "cuda" else "int8",
                            download_root=str(models_dir()))
    except Exception:
        if device == "cuda":   # CUDA libraries missing/broken: fall back to CPU instead of failing
            return WhisperModel(size, device="cpu", compute_type="int8", download_root=str(models_dir()))
        raise


def transcribe(media: Path, size: str = "small", device: str = "auto",
               start: float = 0.0, end: float | None = None) -> list[dict]:
    """Captions are optional: any failure (no model, offline on first use, bad audio) logs a warning and
    returns [] so the clip still renders."""
    try:
        model = _model(size, device)
    except Exception as e:
        log.warning("Speech model unavailable (%s); rendering without captions", e)
        return []
    kwargs = {"word_timestamps": True, "vad_filter": True}
    try:                       # your vocabulary (streamer names, GTA terms) as a hint
        from ..editing import vocab
        if (hint := vocab.prompt()):
            kwargs["initial_prompt"] = hint
    except Exception:          # noqa: BLE001
        pass
    if start or end:
        kwargs["clip_timestamps"] = [start, end] if end else [start]
    words = []
    try:
        segments, info = model.transcribe(str(media), **kwargs)
        transcribe.last_language = getattr(info, "language", None)      # Whisper's language detection
        for seg in segments:
            for w in seg.words or []:
                words.append({"word": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    except Exception as e:
        log.warning("Transcription failed (%s); rendering without captions", e)
        return []
    return words


transcribe.last_language = None


def speech_excitement(words: list[dict], duration_sec: int) -> np.ndarray:
    """Per-second proxy for 'quotable' speech: fast talking + exclamations + reaction words."""
    out = np.zeros(duration_sec, dtype=np.float32)
    for w in words:
        i = int(w["start"])
        if 0 <= i < duration_sec:
            token = w["word"].lower().strip(".,!?")
            out[i] += 1 + (1.5 if token in LAUGH else 0) + (1 if w["word"].endswith("!") else 0)
    return out
