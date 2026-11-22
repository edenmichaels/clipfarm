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
from pathlib import Path

import uvicorn

from . import config, db, redact, secrets
from .jobs import pipeline  # noqa: F401  (registers job handlers)
from .jobs.engine import Engine, enqueue
from . import proc

log = logging.getLogger("clipfarm")


class _RedactingStream:
    """Wraps engine.log/stderr so tracebacks and crash output never contain keys or tokens."""
    def __init__(self, stream):
        self._s = stream

    def write(self, text):
        return self._s.write(redact.redact(text))

    def flush(self):
        return self._s.flush()

    def __getattr__(self, name):
        return getattr(self._s, name)


def _ensure_stdio() -> None:
    """A windowed (no-console) build has no stdout/stderr; uvicorn and print() would crash.
    Send them to a log file in the app data folder instead. Everything written is redacted."""
    if sys.stdout is None or sys.stderr is None:
        f = open(config.app_data_dir() / "engine.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    sys.stdout = _RedactingStream(sys.stdout)
    sys.stderr = _RedactingStream(sys.stderr)


def wipe_user_data() -> int:
    """Called by the uninstaller when "delete the application data" is ticked: removes every ClipFarm key
    from Credential Manager, the encrypted backup and the %APPDATA%\\ClipFarm folder."""
    import shutil
    data = config.app_data_dir()
    ids = []
    try:
        db.init_db()
        ids = [a["id"] for a in db.q("SELECT id FROM accounts")]
    except Exception:
        pass
    n = secrets.wipe_all(ids)
    try:
        db.connect().close()
    except Exception:
        pass
    shutil.rmtree(data, ignore_errors=True)
    return n


def self_test(out_path: str) -> int:
    """`clipfarm-engine.exe --self-test result.json`: checks an installed copy works without a terminal.
    Renders a short clip (layout, captions, hook, credit) with the bundled FFmpeg/font, round-trips a key
    through Credential Manager + vault, and runs the offline health checks. Exit code 0 = pass."""
    import json
    import tempfile
    from pathlib import Path
    from . import health
    from .editing.ffmpeg_util import ffmpeg_bin, probe
    from .editing.render import RenderSpec, render
    res: dict = {"ok": False, "steps": {}}
    try:
        db.init_db()
        res["steps"]["data_dir"] = str(config.app_data_dir())
        res["steps"]["ffmpeg"] = ffmpeg_bin()
        work = Path(tempfile.mkdtemp(prefix="clipfarm self test "))
        src = work / "source clip.mp4"
        proc.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=8",
                        "-f", "lavfi", "-i", "sine=f=330:d=8", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", str(src)], check=True, capture_output=True)
        words = [{"word": w, "start": 1 + i * 0.4, "end": 1.35 + i * 0.4} for i, w in enumerate("no way that worked".split())]
        r = render(RenderSpec(src, 0.5, 7.5, work / "out clip.mp4", layout="blur_fill", words=words,
                              hook="Self test", credit="twitch.tv/clipfarm", quality="fast"))
        info = probe(Path(r["path"]))
        assert (info["width"], info["height"]) == (1080, 1920), info
        res["steps"]["render"] = {"encoder": r["encoder"], "duration": info["duration"], "layout": r["layout"]}
        # Captions centred in the real install (bundled FFmpeg + fonts): render with/without, measure.
        import numpy as np
        flat = work / "flat clip.mp4"
        proc.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x404040:s=1920x1080:r=30:d=5",
                        "-f", "lavfi", "-i", "sine=f=220:d=5", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", str(flat)], check=True, capture_output=True)
        cw = [{"word": w, "start": 1 + i * 0.4, "end": 1.35 + i * 0.4} for i, w in enumerate("WAIT FOR IT".split())]
        frames = []
        for name, ws in (("with", cw), ("without", [])):
            rr = render(RenderSpec(flat, 0, 4, work / f"cap {name}.mp4", layout="crop", words=ws, quality="fast",
                                   punch_zoom=False, progress_bar=False))
            raw = proc.run([ffmpeg_bin(), "-v", "error", "-ss", "1.5", "-i", rr["path"], "-frames:v", "1",
                                  "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
            frames.append(np.frombuffer(raw, np.uint8).reshape(1920, 1080).astype(int))
        ys, xs = np.nonzero(np.abs(frames[0] - frames[1]) > 30)
        off = abs((xs.min() + xs.max()) / 2 - 540) / 1080 if xs.size else 1.0
        res["steps"]["captions"] = {"x": [int(xs.min()), int(xs.max())] if xs.size else None,
                                    "centre_offset_pct": round(off * 100, 2)}
        assert off <= 0.02, f"captions off-centre by {off:.1%}"
        secrets.set_secret("selftest_key", "selftest-value-123")
        assert secrets.get_secret("selftest_key") == "selftest-value-123"
        secrets.delete_secret("selftest_key")
        res["steps"]["keys"] = {"keychain": secrets.keychain_backend if secrets.keyring else None,
                                "error": secrets.keychain_error}
        checks = health.run_all(["engine", "storage", "keys", "ffmpeg", "font", "gpu"])
        res["steps"]["health"] = {c["id"]: c["status"] for c in checks}
        res["ok"] = all(c["status"] != "red" for c in checks)
    except Exception as e:
        import traceback
        res["error"] = redact.redact(f"{e}\n{traceback.format_exc()}")
    Path(out_path).write_text(json.dumps(res, indent=2), encoding="utf-8")
    return 0 if res["ok"] else 1


def _startup_jobs() -> None:
    """Restore keys, then queue the quiet start-up checks and the recurring jobs (once each)."""
    try:
        ids = [a["id"] for a in db.q("SELECT id FROM accounts")]
        res = secrets.restore(ids)
        if res["restored_to_keychain"] or res["backed_up_to_vault"]:
            db.log("info", "keys", f"Restored {res['restored_to_keychain']} key(s) from the encrypted backup; "
                                   f"backed up {res['backed_up_to_vault']} key(s).")
    except Exception as e:
        db.log("error", "keys", f"Key restore failed: {e}")

    def once(kind: str, delay: int = 0, priority: int = 5, payload: dict | None = None) -> None:
        if not db.q1("SELECT 1 FROM jobs WHERE kind=? AND status IN ('queued','running')", (kind,)):
            enqueue(kind, payload or {}, priority=priority, delay_sec=delay)

    once("health_check", delay=3, priority=1)
    once("post_due", delay=60, priority=3)
    once("metrics", delay=600, priority=8)
    if (db.get_setting("autopilot") or {}).get("enabled"):
        once("autopilot_tick", delay=20, priority=1)


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


_lock_file = None


def acquire_single_instance(path: Path | None = None, wait_sec: float = 15.0) -> bool:
    """Only one engine (one job loop) may run per data folder. A second engine waits briefly (the old one may be
    shutting down after an update or restart) and then gives up. The OS releases the lock if the engine dies."""
    global _lock_file
    path = path or (config.app_data_dir() / "engine.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    deadline = time.time() + wait_sec
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() >= deadline:
                f.close()
                return False
            time.sleep(0.5)
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    _lock_file = f
    return True


def _watch_parent() -> None:
    """Exit if the desktop shell dies (e.g. killed from Task Manager) so no orphan engine is left."""
    pid = int(os.environ.get("CLIPFARM_PARENT_PID", "0"))
    if not pid:
        return

    def loop():
        while True:
            time.sleep(5)
            if not _parent_alive(pid):
                from . import proc
                proc.kill_all()
                os._exit(0)
    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def main() -> None:
    if "--wipe-user-data" in sys.argv:
        n = wipe_user_data()
        print(f"Removed {n} key(s) and the ClipFarm data folder.")
        return
    if "--self-test" in sys.argv:
        i = sys.argv.index("--self-test")
        out = sys.argv[i + 1] if len(sys.argv) > i + 1 else str(config.app_data_dir() / "self-test.json")
        sys.exit(self_test(out))
    _ensure_stdio()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    redact.install_logging_filter()
    if not acquire_single_instance():
        print("CLIPFARM_ALREADY_RUNNING another ClipFarm engine is using this data folder", flush=True)
        sys.exit(3)
    from . import proc
    proc.install_guard()                    # no console windows for any child process (Windows)
    proc.kill_children_when_engine_dies()   # Windows: ffmpeg etc. can't outlive the engine
    _watch_parent()
    db.init_db()
    _startup_jobs()
    workers = int(os.environ.get("CLIPFARM_WORKERS", "2"))
    engine = Engine(workers=workers, render_workers=1)
    engine.start()

    from .api import app

    print(f"CLIPFARM_STARTING {config.API_PORT}", flush=True)
    try:
        # log_config=None: keep uvicorn from reconfiguring logging (it assumes a real console).
        uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, log_level="warning", log_config=None)
    finally:
        engine.stop()


if __name__ == "__main__":
    main()
