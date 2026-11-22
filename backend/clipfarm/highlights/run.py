"""Run highlight detection on one source, in three stages (shared by the pipeline and the evaluation).

  1. whole source: audio events (YAMNet), loudness z-score, chat reactions, community clips
  2. shortlisted windows only: visual signals from a low-res copy (motion, cuts, flashes, shake, menus)
  3. final shortlist only: transcript + LLM rating
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from . import audio_events, detect, signals


def detect_source(audio_media: Path, *, chat: list[tuple[float, str]] | None = None,
                  clips: list[tuple[float, int]] | None = None, budget: int = 4, min_len: float = 15,
                  max_len: float = 60, focus: str = "balanced", weights: dict | None = None,
                  visual_fn: Callable[[float, float], dict[str, np.ndarray] | None] | None = None,
                  words_fn: Callable[[float, float], list[dict]] | None = None,
                  rate_fn: Callable[[str, str], dict | None] | None = None,
                  threshold: float = 0.42, log: Callable[[str], None] = lambda m: None) -> tuple[list[detect.Moment], dict]:
    loud = signals.audio_rms(audio_media)
    n = len(loud)
    ev = audio_events.analyse(audio_media)
    if ev is None:
        log("Sound-event model not installed: using chat, visuals and community clips only.")
    sig = detect.Signals(n, events=ev, loudness=loud,
                         chat=signals.chat_reactions(chat, n) if chat else None, clips=clips or [])
    comp, parts = detect.components(sig, focus)
    cv = detect.curve(comp, parts, weights)
    shortlist = detect.pick(cv, parts, comp, loud, budget=budget * 3, min_len=min_len, max_len=max_len,
                            threshold=threshold * 0.75)
    if visual_fn and shortlist:
        vis = {k: np.zeros(n, np.float32) for k in ("motion", "cuts", "flash", "shake", "static", "dark", "ui")}
        for m in shortlist:
            a, b = max(0, int(m.start) - 8), min(n, int(m.end) + 8)
            try:
                v = visual_fn(a, b)
            except Exception as e:     # a failed download mustn't sink the whole source
                log(f"Visual check skipped for {a}-{b}s: {e}")
                v = None
            for k, arr in (v or {}).items():
                if k in vis and arr is not None:
                    seg = np.asarray(arr, np.float32)[: b - a]
                    vis[k][a:a + seg.size] = seg
        sig.visual = vis
        comp, parts = detect.components(sig, focus)
        cv = detect.curve(comp, parts, weights)
        shortlist = detect.pick(cv, parts, comp, loud, budget=budget * 2, min_len=min_len, max_len=max_len,
                                threshold=threshold * 0.85)
    final = []
    for m in shortlist:
        if rate_fn and words_fn:
            words = words_fn(m.start, m.end)
            m.signals["_words"] = words
            detected = m.reason
            m = detect.rescore_with_llm(m, rate_fn(" ".join(w["word"] for w in words), detected), weights)
        if m.score >= threshold:
            final.append(m)
    final = sorted(final, key=lambda m: m.score, reverse=True)[:budget]
    return sorted(final, key=lambda m: m.start), {"curve": cv, "components": comp, "parts": parts, "duration": n}
