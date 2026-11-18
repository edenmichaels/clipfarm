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


def complete_json(system: str, user: str, timeout: float = 60) -> dict:
    cfg = db.get_setting("llm", {})
    provider = cfg.get("provider", "ollama")
    try:
        if provider == "ollama":
            r = requests.post(f"{cfg.get('base_url', 'http://127.0.0.1:11434')}/api/chat", timeout=timeout, json={
                "model": cfg.get("model", "llama3.1:8b"), "stream": False, "format": "json",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            r.raise_for_status()
            text = r.json()["message"]["content"]
        else:
            key = secrets.get_secret("llm_api_key")
            if not key:
                raise LLMUnavailable("No cloud LLM API key set.")
            r = requests.post(f"{cfg.get('cloud_base_url')}/chat/completions", timeout=timeout,
                              headers={"Authorization": f"Bearer {key}"}, json={
                                  "model": cfg.get("cloud_model"), "response_format": {"type": "json_object"},
                                  "messages": [{"role": "system", "content": system},
                                               {"role": "user", "content": user}]})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
    except requests.RequestException as e:
        raise LLMUnavailable(str(e)) from e
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMUnavailable("Model did not return JSON")
    return json.loads(m.group(0))
