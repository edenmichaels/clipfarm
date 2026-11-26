"""API keys and OAuth tokens.

Two copies, kept in sync:
  1. OS keychain (Windows Credential Manager) under the fixed service name "ClipFarm".
     The uninstaller never touches it.
  2. Encrypted backup in %APPDATA%\\ClipFarm\\vault.dat (DPAPI, current user only). See vault.py.

On every start `restore()` puts back any key that is missing from the keychain but present in the
vault, and backs up any keychain key the vault doesn't have yet (installs from before v0.2).
Nothing secret is ever written to SQLite, settings, logs or crash reports in plain text.
"""
from __future__ import annotations

import json
import logging
import os
import sys

from . import redact, vault

SERVICE = os.environ.get("CLIPFARM_KEYRING_SERVICE", "ClipFarm")   # override only in tests

# Fixed secrets the Settings screen manages; OAuth tokens are stored as "account:<id>".
SECRET_NAMES = ["twitch_client_id", "twitch_client_secret", "youtube_api_key", "kick_client_id",
                "kick_client_secret", "google_oauth_client_id", "google_oauth_client_secret", "llm_api_key",
                "tiktok_client_key", "tiktok_client_secret", "meta_app_id", "meta_app_secret",
                "x_api_key", "x_api_secret", "x_access_token", "x_access_secret"]

log = logging.getLogger("clipfarm.secrets")

keyring = None
keychain_backend = "none"
keychain_error: str | None = None
try:
    import keyring as _kr  # type: ignore
    # A packaged (PyInstaller) build loses keyring's backend discovery, so pick the OS backend
    # explicitly. Windows: Credential Manager. macOS: Keychain.
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring  # type: ignore
        _kr.set_keyring(WinVaultKeyring())
    elif sys.platform == "darwin":
        from keyring.backends.macOS import Keyring as _MacKeyring  # type: ignore
        _kr.set_keyring(_MacKeyring())
    _backend = _kr.get_keyring()
    keychain_backend = type(_backend).__name__
    # keyring's "fail" backend (no usable keychain) must not count as working.
    if "fail" in type(_backend).__module__ or "Null" in keychain_backend:
        keychain_error = f"No usable OS keychain ({keychain_backend})"
    else:
        keyring = _kr
except Exception as e:  # ImportError, or a broken backend in a frozen build
    keychain_error = f"Keychain unavailable: {e}"


def _kc_set(name: str, data: str) -> bool:
    if not keyring:
        return False
    try:
        keyring.set_password(SERVICE, name, data)
        return True
    except Exception as e:
        log.warning("Keychain write failed for %s: %s", name, type(e).__name__)
        return False


def _kc_get(name: str) -> str | None:
    if not keyring:
        return None
    try:
        return keyring.get_password(SERVICE, name)
    except Exception:
        return None


def _kc_del(name: str) -> None:
    if keyring:
        try:
            keyring.delete_password(SERVICE, name)
        except Exception:
            pass


def set_secret(name: str, value: str | dict) -> None:
    data = json.dumps(value) if isinstance(value, dict) else value
    redact.register(value)
    _kc_set(name, data)
    vault.put(name, data)            # update both copies whenever a key changes


def get_secret(name: str, as_json: bool = False):
    data = _kc_get(name)
    if data is None:
        try:
            data = vault.load().get(name)
        except Exception:
            data = None
        if data is not None:
            _kc_set(name, data)       # silently restore the keychain copy
    if data is None:
        return None
    redact.register(data)
    if as_json:
        try:
            v = json.loads(data)
        except ValueError:
            return None
        redact.register(v)
        return v
    return data


def delete_secret(name: str) -> None:
    _kc_del(name)
    vault.remove(name)


def has_secret(name: str) -> bool:
    return get_secret(name) is not None


def known_names(extra: list[str] | None = None) -> list[str]:
    names = list(SECRET_NAMES)
    try:
        names += list(vault.load().keys())
    except Exception:
        pass
    for n in extra or []:
        if n not in names:
            names.append(n)
    return list(dict.fromkeys(names))


def restore(account_ids: list[int] | None = None) -> dict:
    """Run on every start. Returns counts only (never values)."""
    restored = backed_up = 0
    try:
        stored = vault.load()
    except Exception as e:
        log.warning("Vault unreadable: %s", e)
        stored = {}
    for name, data in stored.items():
        redact.register(data)
        if _kc_get(name) is None and _kc_set(name, data):
            restored += 1
    for name in known_names([f"account:{i}" for i in account_ids or []]):
        if name in stored:
            continue
        data = _kc_get(name)
        if data is not None:
            redact.register(data)
            vault.put(name, data)
            backed_up += 1
    return {"restored_to_keychain": restored, "backed_up_to_vault": backed_up,
            "keychain": keychain_backend if keyring else None, "keychain_error": keychain_error}


def all_secrets(account_ids: list[int] | None = None) -> dict[str, str]:
    out = {}
    for name in known_names([f"account:{i}" for i in account_ids or []]):
        v = get_secret(name)
        if v is not None:
            out[name] = v
    return out


def wipe_all(account_ids: list[int] | None = None) -> int:
    """Uninstaller "delete my settings and keys": remove every ClipFarm key from the keychain + vault."""
    n = 0
    for name in known_names([f"account:{i}" for i in account_ids or []]):
        if _kc_get(name) is not None:
            n += 1
        _kc_del(name)
    p = vault.vault_path()
    if p.exists():
        p.unlink()
    return n
