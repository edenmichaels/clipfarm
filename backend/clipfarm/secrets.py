"""Credential storage in the OS keychain (Windows Credential Manager / macOS Keychain).

Nothing secret is written to SQLite or to disk in plain text.
"""
from __future__ import annotations

import json

SERVICE = "ClipFarm"

try:
    import keyring  # type: ignore
except ImportError:  # keyring is in requirements.txt; this keeps tests runnable without it
    keyring = None

_memory_store: dict[str, str] = {}  # fallback for tests / CI only


def set_secret(name: str, value: str | dict) -> None:
    data = json.dumps(value) if isinstance(value, dict) else value
    if keyring:
        keyring.set_password(SERVICE, name, data)
    else:
        _memory_store[name] = data


def get_secret(name: str, as_json: bool = False):
    data = keyring.get_password(SERVICE, name) if keyring else _memory_store.get(name)
    if data is None:
        return None
    return json.loads(data) if as_json else data


def delete_secret(name: str) -> None:
    if keyring:
        try:
            keyring.delete_password(SERVICE, name)
        except Exception:
            pass
    _memory_store.pop(name, None)


def has_secret(name: str) -> bool:
    return get_secret(name) is not None
