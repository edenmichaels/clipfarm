"""Multi-language captions: translate the caption chunks with the LLM and keep word timing.

Each chunk (1–3 words on screen) is translated as part of its sentence; the translated words share the
original chunk's time span, so captions stay in sync with the speech.
"""
from __future__ import annotations

from ..editing.captions import chunk_words
from ..llm.client import LLMUnavailable, complete_json

LANGUAGES = {"en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French", "de": "German", "it": "Italian",
             "hi": "Hindi", "ar": "Arabic", "ja": "Japanese", "ko": "Korean", "id": "Indonesian", "tl": "Filipino",
             "vi": "Vietnamese", "ru": "Russian", "tr": "Turkish"}


def translate_words(words: list[dict], target: str, source: str | None = None, max_words: int = 3) -> list[dict]:
    if target not in LANGUAGES:
        raise ValueError(f"Unsupported language: {target}")
    live = [w for w in words if not w.get("deleted")]
    chunks = chunk_words([{**w, "start": float(w["start"]), "end": float(w["end"])} for w in live], 6, gap=0.8)
    lines = [" ".join(w["word"] for w in c) for c in chunks]
    system = (f"Translate gaming-stream captions into {LANGUAGES[target]}. Keep slang natural for young viewers, "
              "keep names, keep it short. Return JSON {\"lines\": [...]} with exactly one translated line per input "
              "line, in order.")
    try:
        out = complete_json(system, "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines)), timeout=90)
    except LLMUnavailable as e:
        raise RuntimeError("Translation needs your AI model (Ollama or the configured endpoint).") from e
    tr = out.get("lines") or []
    if len(tr) != len(lines):
        raise RuntimeError("The AI model returned the wrong number of lines; try again.")
    res = []
    for c, text in zip(chunks, tr):
        toks = str(text).split() or ["…"]
        a, b = c[0]["start"], c[-1]["end"]
        step = (b - a) / len(toks)
        for k, tok in enumerate(toks):
            res.append({"word": tok, "start": round(a + k * step, 3), "end": round(a + (k + 1) * step - 0.02, 3)})
    return res
