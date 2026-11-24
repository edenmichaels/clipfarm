"""LLM client: local (Ollama) or any OpenAI-compatible cloud endpoint.

Settings > AI model picks the provider. Cloud API keys come from the keychain ("llm_api_key").
"""
from __future__ import annotations

import json
import re

import requests

from .. import db, secrets


class LLMUnavailable(RuntimeError):
    pass


def complete_json(system: str, user: str, timeout: float = 60, images: list[bytes] | None = None) -> dict:
    """Ask the configured model for JSON. `images` (small JPEGs) are sent to vision models; if the model refuses
    them, the question is asked again as text only."""
    if images:
        try:
            return _complete(system, user, timeout, images)
        except LLMUnavailable:
            pass
    return _complete(system, user, timeout, None)


def _complete(system: str, user: str, timeout: float, images: list[bytes] | None) -> dict:
    import base64
    cfg = db.get_setting("llm", {})
    provider = cfg.get("provider", "ollama")
    b64 = [base64.b64encode(i).decode() for i in images or []]
    try:
        if provider == "ollama":
            um = {"role": "user", "content": user, **({"images": b64} if b64 else {})}
            r = requests.post(f"{cfg.get('base_url', 'http://127.0.0.1:11434')}/api/chat", timeout=timeout, json={
                "model": cfg.get("model", "llama3.1:8b"), "stream": False, "format": "json",
                "messages": [{"role": "system", "content": system}, um]})
            r.raise_for_status()
            text = r.json()["message"]["content"]
        else:
            key = secrets.get_secret("llm_api_key")
            if not key:
                raise LLMUnavailable("No cloud LLM API key set.")
            content = user if not b64 else [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{x}"}} for x in b64]
            r = requests.post(f"{cfg.get('cloud_base_url')}/chat/completions", timeout=timeout,
                              headers={"Authorization": f"Bearer {key}"}, json={
                                  "model": cfg.get("cloud_model"), "response_format": {"type": "json_object"},
                                  "messages": [{"role": "system", "content": system},
                                               {"role": "user", "content": content}]})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, ValueError) as e:
        raise LLMUnavailable(str(e)) from e
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMUnavailable("Model did not return JSON")
    try:
        out = json.loads(m.group(0))
    except ValueError as e:
        raise LLMUnavailable(f"Model returned broken JSON: {e}") from e
    if not isinstance(out, dict):
        raise LLMUnavailable("Model did not return a JSON object")
    return out
