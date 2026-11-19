"""Encrypted backup of every API key and OAuth token: %APPDATA%\\ClipFarm\\vault.dat.

Windows: encrypted with DPAPI (CryptProtectData, current user only). The file is useless on any
other PC or Windows account. Other OSes (dev/CI only): AES-GCM with a key file next to the vault.

The vault mirrors the OS keychain (Windows Credential Manager). If a key goes missing from the
keychain, it is restored from the vault on the next start. %APPDATA%\\ClipFarm sits outside the
install folder, so uninstalling or updating ClipFarm leaves it alone unless the user ticks the
uninstaller's delete-data box.

Also: password-protected export/import (scrypt + AES-GCM) for moving to a new PC.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
from pathlib import Path

from . import config

MAGIC = b"CFV1"
_lock = threading.Lock()
_ENTROPY = b"ClipFarm vault v1"


# ---- protectors ------------------------------------------------------------------------------
class DPAPIProtector:
    name = "Windows DPAPI (current user)"

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        self._ctypes, self._BLOB = ctypes, BLOB
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def _blob(self, data: bytes):
        buf = self._ctypes.create_string_buffer(data, len(data))
        return self._BLOB(len(data), self._ctypes.cast(buf, self._ctypes.POINTER(self._ctypes.c_char))), buf

    def _call(self, fn, data: bytes) -> bytes:
        ctypes = self._ctypes
        inp, _keep = self._blob(data)
        ent, _keep2 = self._blob(_ENTROPY)
        out = self._BLOB()
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        if not fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, CRYPTPROTECT_UI_FORBIDDEN,
                  ctypes.byref(out)):
            raise OSError(f"DPAPI call failed (error {ctypes.GetLastError()})")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            self._kernel32.LocalFree(out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call(lambda i, d, e, r, p, f, o: self._crypt32.CryptProtectData(i, "ClipFarm", e, r, p, f, o),
                          data)

    def unprotect(self, data: bytes) -> bytes:
        return self._call(self._crypt32.CryptUnprotectData, data)


class KeyFileProtector:
    """Non-Windows fallback (development and CI only): AES-GCM with a 0600 key file."""
    name = "AES-GCM key file (development only)"

    def __init__(self, key_path: Path):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._AESGCM = AESGCM
        if not key_path.exists():
            key_path.write_bytes(AESGCM.generate_key(bit_length=256))
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
        self.key = key_path.read_bytes()

    def protect(self, data: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._AESGCM(self.key).encrypt(nonce, data, _ENTROPY)

    def unprotect(self, data: bytes) -> bytes:
        return self._AESGCM(self.key).decrypt(data[:12], data[12:], _ENTROPY)


_protector = None


def protector():
    global _protector
    if _protector is None:
        _protector = DPAPIProtector() if sys.platform == "win32" else \
            KeyFileProtector(config.app_data_dir() / "vault.key")
    return _protector


def set_protector(p) -> None:
    """Tests only."""
    global _protector
    _protector = p


# ---- vault file ------------------------------------------------------------------------------
def vault_path() -> Path:
    return config.app_data_dir() / "vault.dat"


def load() -> dict[str, str]:
    p = vault_path()
    if not p.exists():
        return {}
    raw = p.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError("vault.dat is not a ClipFarm vault")
    data = json.loads(protector().unprotect(raw[len(MAGIC):]).decode("utf-8"))
    return dict(data.get("secrets", {}))


def save(secrets_map: dict[str, str]) -> None:
    blob = MAGIC + protector().protect(json.dumps({"version": 1, "secrets": secrets_map}).encode("utf-8"))
    p = vault_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, p)


def put(name: str, value: str) -> None:
    with _lock:
        cur = _safe_load()
        if cur.get(name) != value:
            cur[name] = value
            save(cur)


def remove(name: str) -> None:
    with _lock:
        cur = _safe_load()
        if name in cur:
            del cur[name]
            save(cur)


def _safe_load() -> dict[str, str]:
    try:
        return load()
    except Exception:
        # Corrupt or from another Windows account: keep a copy and start a fresh vault.
        p = vault_path()
        if p.exists():
            os.replace(p, p.with_suffix(".unreadable"))
        return {}


def status() -> dict:
    p = vault_path()
    try:
        n = len(load())
        ok = True
        err = None
    except Exception as e:
        n, ok, err = 0, False, str(e)
    return {"path": str(p), "exists": p.exists(), "readable": ok, "count": n, "error": err,
            "protector": protector().name}


# ---- password-protected export / import (new PC) ------------------------------------------
def export_bundle(secrets_map: dict[str, str], settings: dict, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    if len(password) < 8:
        raise ValueError("Use a password of at least 8 characters.")
    salt, nonce = os.urandom(16), os.urandom(12)
    key = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(password.encode("utf-8"))
    body = json.dumps({"version": 1, "secrets": secrets_map, "settings": settings}).encode("utf-8")
    ct = AESGCM(key).encrypt(nonce, body, b"ClipFarm export v1")
    return json.dumps({"format": "clipfarm-export", "version": 1, "kdf": "scrypt", "n": 2 ** 15, "r": 8, "p": 1,
                       "salt": _b64(salt), "nonce": _b64(nonce), "data": _b64(ct)}).encode("utf-8")


def import_bundle(blob: bytes, password: str) -> dict:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    try:
        d = json.loads(blob.decode("utf-8"))
        assert d.get("format") == "clipfarm-export"
    except Exception as e:
        raise ValueError("That isn't a ClipFarm export file.") from e
    key = Scrypt(salt=_unb64(d["salt"]), length=32, n=d["n"], r=d["r"], p=d["p"]).derive(password.encode("utf-8"))
    try:
        body = AESGCM(key).decrypt(_unb64(d["nonce"]), _unb64(d["data"]), b"ClipFarm export v1")
    except InvalidTag as e:
        raise ValueError("Wrong password, or the file is damaged.") from e
    return json.loads(body.decode("utf-8"))


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)
