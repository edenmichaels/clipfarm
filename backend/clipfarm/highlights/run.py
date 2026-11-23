"""Run highlight detection on one source, in three stages (shared by the pipeline and the evaluation).

  1. whole source: audio events (YAMNet), loudness z-score, chat reactions, community clips
  2. shortlisted windows only: visual signals from a low-res copy (motion, cuts, flashes, shake, menus)
  3. final shortlist only: transcript + LLM rating
  4. the viral model: features (reaction, chat, event tags, surprise, speech, context, mundane) -> viral score
     0-100, moment type, event tags, plain-English reason and each feature's contribution. Final ranking.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from . import audio_events, detect, events, features, signals, viral_model


def detect_source(audio_media: Path, *, chat: list[tuple[float, str]] | None = None,
                  clips: list[tuple[float, int]] | None = None, budget: int = 4, min_len: float = 15,
                  max_len: float = 60, focus: str = "balanced", weights: dict | None = None,
                  visual_fn: Callable[[float, float], dict[str, np.ndarray] | None] | None = None,
                  words_fn: Callable[[float, float], list[dict]] | None = None,
                  rate_fn: Callable[[str, str], dict | None] | None = None,
                  threshold: float = 0.42, log: Callable[[str], None] = lambda m: None,
                  game_id: int | None = None, viral_threshold: int = 35, model: dict | None = None,
                  vision_fn: Callable[[float, float], list[str] | None] | None = None
                  ) -> tuple[list[detect.Moment], dict]:
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
        vis = {k: np.zeros(n, np.float32) for k in ("motion", "cuts", "flash", "shake", "static", "dark", "ui",
                                                    "face_motion")}
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
    if "face_motion" in parts and not np.asarray(parts["face_motion"]).any():
        parts.pop("face_motion")
    mdl = model or viral_model.load(game_id)
    trend = viral_model.trend_tags(game_id) if game_id is not None else {}
    all_words: list[dict] = []
    staged = []
    for m in shortlist:
        if words_fn:
            try:
                words = words_fn(m.start, m.end)
            except Exception as e:            # noqa: BLE001
                log(f"Transcript skipped for {m.start:.0f}s: {e}")
                words = []
            m.signals["_words"] = words
            all_words += words
            if rate_fn:
                m = detect.rescore_with_llm(m, rate_fn(" ".join(w["word"] for w in words), m.reason), weights)
        staged.append(m)
    # stage 4: viral model
    tags = events.from_signals(n, parts, all_words, chat)
    extra = features.per_second(parts, comp, n, all_words)
    rate_z = parts.get("chat_rate_z")
    final = []
    for m in staged:
        a, b = int(m.start), int(np.ceil(m.end))
        if vision_fn:
            vt = vision_fn(m.start, m.end)
            for t in vt or []:
                if t in tags:
                    seg = tags[t][a:b]
                    seg[:] = np.maximum(seg, 0.6)
        f = features.window(parts, comp, extra, tags, a, b, m.signals.get("_llm"), trend)
        res = viral_model.score(f, mdl)
        tg = events.summarise(tags, a, b)
        ratio = None
        if rate_z is not None and chat:
            base = max(0.2, len(chat) / max(1, n))
            busiest = max((sum(1 for t, _ in chat if s <= t < s + 5) / 5 for s in range(a, b, 2)), default=0)
            ratio = busiest / base
        top_trend = next((k for k in trend if f["trend"] > 0 and any(x["tag"] == k for x in tg)), None)
        m.viral = res["score"]
        m.features = f
        m.tags = tg
        m.type = viral_model.moment_type(f, (m.signals.get("_llm") or {}).get("type"))
        m.reason = f"{m.type}: " + viral_model.reason(f, res, tg, chat_ratio=ratio, trend_tag=top_trend)
        m.why = viral_model.explain(f, res)
        m.signals["viral"] = res["score"]
        m = _setup_bounds(m, tags, parts, n, min_len, max_len)
        if m.viral >= viral_threshold:
            final.append(m)
    final = sorted(final, key=lambda m: (m.viral or 0, m.score), reverse=True)[:budget]
    return sorted(final, key=lambda m: m.start), {"curve": cv, "components": comp, "parts": parts, "duration": n,
                                                  "tags": tags, "staged": staged, "model": mdl.get("game_id")}


def _setup_bounds(m: detect.Moment, tags: dict, parts: dict, n: int, min_len: float, max_len: float
                  ) -> detect.Moment:
    """Start 1-3 s before the setup (the first thing that happens before the reaction), keep the event and the
    full reaction. If there's a long setup (RP conversation, a heist plan) before that, offer it as a variant."""
    peak = int(m.peak)
    onset = None
    for k, v in tags.items():
        seg = v[max(0, peak - 12):peak + 1]
        hits = np.nonzero(seg >= 0.45)[0]
        if hits.size:
            t = max(0, peak - 12) + int(hits[0])
            onset = t if onset is None else min(onset, t)
    if onset is not None and onset - 2 < m.start and m.end - (onset - 2) <= max_len:
        m.start = float(max(0, onset - 2))
    speech = np.asarray(parts.get("speech", np.zeros(n)), np.float32)
    i = int(m.start) - 1
    while i > 0 and speech[i] > 0.4 and m.start - i < 40:
        i -= 1
    if m.start - i >= 10:
        m.signals["setup_start"] = float(max(0, i + 1))      # "with setup" variant
    return m
