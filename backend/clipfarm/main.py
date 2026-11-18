"""Sidecar entry point. Launched by the Tauri shell (or `python -m clipfarm.main` in dev).

Prints `CLIPFARM_READY <port>` on stdout once the API is listening so the shell knows when
to load the UI.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

import uvicorn

from . import config, db
from .jobs import pipeline  # noqa: F401  (registers job handlers)
from .jobs.engine import Engine


def _ensure_stdio() -> None:
    """A windowed (no-console) build has no stdout/stderr; uvicorn and print() would crash.
    Send them to a log file in the app data folder instead."""
    if sys.stdout is None or sys.stderr is None:
        log = open(config.app_data_dir() / "engine.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def _parent_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT = still running
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _watch_parent() -> None:
    """Exit if the desktop shell dies (e.g. killed from Task Manager) so no orphan engine is left."""
    pid = int(os.environ.get("CLIPFARM_PARENT_PID", "0"))
    if not pid:
        return

    def loop():
        while True:
            time.sleep(5)
            if not _parent_alive(pid):
                os._exit(0)
    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def main() -> None:
    _ensure_stdio()
    _watch_parent()
    db.init_db()
    workers = int(os.environ.get("CLIPFARM_WORKERS", "2"))
    engine = Engine(workers=workers, render_workers=1)
    engine.start()

    from .api import app

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    print(f"CLIPFARM_STARTING {config.API_PORT}", flush=True)
    try:
        # log_config=None: keep uvicorn from reconfiguring logging (it assumes a real console).
        uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, log_level="warning", log_config=None)
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
