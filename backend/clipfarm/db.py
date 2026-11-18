"""SQLite access layer. Thin on purpose: plain SQL, one connection per thread."""
from __future__ import annotations

import json
import sqlite3
import threading
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

from . import config

SCHEMA_VERSION = 1
_local = threading.local()
_db_file: Path | None = None


def init_db(path: Path | None = None) -> None:
    """Create tables and default settings. Safe to call repeatedly."""
    global _db_file
    _db_file = Path(path) if path else config.db_path()
    conn = connect()
    schema = resources.files("clipfarm").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    if not conn.execute("SELECT 1 FROM schema_version").fetchone():
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
    for key, value in config.DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))
    conn.commit()


def connect() -> sqlite3.Connection:
    """Per-thread connection (sqlite3 connections aren't thread-safe to share)."""
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "file", None) != _db_file:
        if _db_file is None:
            raise RuntimeError("init_db() has not been called")
        conn = sqlite3.connect(_db_file, timeout=30, isolation_level=None)  # autocommit; use tx() for batches
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn, _local.file = conn, _db_file
    return conn


def q(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in connect().execute(sql, tuple(params)).fetchall()]


def q1(sql: str, params: Iterable[Any] = ()) -> dict | None:
    row = connect().execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def ex(sql: str, params: Iterable[Any] = ()) -> int:
    """Execute a write; returns lastrowid."""
    return connect().execute(sql, tuple(params)).lastrowid


class tx:
    """`with db.tx():` groups writes into one transaction."""
    def __enter__(self):
        connect().execute("BEGIN IMMEDIATE")
    def __exit__(self, exc_type, *_):
        connect().execute("ROLLBACK" if exc_type else "COMMIT")


# ---- settings helpers -------------------------------------------------------
def get_setting(key: str, default: Any = None) -> Any:
    row = q1("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value: Any) -> None:
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, json.dumps(value)))


def all_settings() -> dict:
    return {r["key"]: json.loads(r["value"]) for r in q("SELECT key, value FROM settings")}


def log(level: str, source: str, message: str, job_id: int | None = None) -> None:
    ex("INSERT INTO logs(level, source, message, job_id) VALUES (?,?,?,?)", (level, source, message, job_id))
