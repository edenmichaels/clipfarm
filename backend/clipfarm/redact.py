"""Keep secrets out of logs, the database, diagnostics and crash reports.

Every known secret value is registered here when it is stored or read, and `redact()` replaces it
(plus anything that looks like a token or key in a URL or JSON) with [REDACTED].
"""
from __future__ import annotations

import logging
import re
import threading

_known: set[str] = set()
_lock = threading.Lock()

_PATTERNS = [
    # key=..., access_token=..., client_secret=..., refresh_token=... in URLs/forms
    re.compile(r"(?i)\b((?:(?:api_?)?key|access_token|refresh_token|id_token|client_secret|token|code|password)=)"
               r"([^&\s\"']{6,})"),
    # "access_token": "..." in JSON
    re.compile(r"(?i)(\"(?:access_token|refresh_token|id_token|client_secret|api_key|password|token)\"\s*:\s*\")"
               r"([^\"]{6,})"),
    re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9._\-~+/=]{8,})"),
    re.compile(r"(?i)(X-ClipFarm-Token[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9\-]{8,})"),
    re.compile(r"\b(AIza)[0-9A-Za-z_\-]{30,}"),          # Google API keys
]


def register(value) -> None:
    """Remember a secret value so it is scrubbed wherever it shows up."""
    if isinstance(value, dict):
        for v in value.values():
            register(v)
        return
    if isinstance(value, str) and len(value) >= 6:
        with _lock:
            _known.add(value)


def redact(text) -> str:
    if text is None:
        return text
    s = str(text)
    with _lock:
        known = sorted(_known, key=len, reverse=True)
    for v in known:
        if v in s:
            s = s.replace(v, "[REDACTED]")
    for p in _PATTERNS:
        s = p.sub(lambda m: m.group(1) + "[REDACTED]", s)
    return s


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


def install_logging_filter() -> None:
    f = RedactingFilter()
    root = logging.getLogger()
    root.addFilter(f)
    for h in root.handlers:
        h.addFilter(f)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging.getLogger(name).addFilter(f)
