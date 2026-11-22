"""The one place ClipFarm starts external programs (ffmpeg, ffprobe, ...).

Why: the engine is a windowless app (PyInstaller --noconsole). On Windows, every console program a windowless
process starts gets a brand-new console window unless it's told not to, and with Windows Terminal as the default
terminal that window pops up and steals focus. So every child process here is started with
CREATE_NO_WINDOW plus STARTUPINFO(SW_HIDE), never through a shell, and its stderr goes to the app's log.

Also:
- install_guard() makes the same flags the default for any library that starts processes itself (yt-dlp runs
  ffmpeg for downloads), so nothing can open a console by accident.
- Every child is tracked; kill_all() stops them on Pause/Quit, and on Windows the engine puts itself in a Job
  Object that kills all children if the engine itself dies or is killed.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import weakref
from typing import Any, Sequence

log = logging.getLogger("clipfarm.proc")
IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_children: "weakref.WeakSet[subprocess.Popen]" = weakref.WeakSet()
_lock = threading.Lock()
_guard_installed = False


def _hidden_startupinfo() -> Any:
    si = subprocess.STARTUPINFO()                       # type: ignore[attr-defined]
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW       # type: ignore[attr-defined]
    si.wShowWindow = 0                                  # SW_HIDE
    return si


def hidden_kwargs(kw: dict | None = None) -> dict:
    """kwargs for subprocess that never show a window. No-op outside Windows."""
    kw = dict(kw or {})
    if kw.get("shell"):
        raise ValueError("ClipFarm never starts programs through a shell")
    if IS_WIN:
        kw["creationflags"] = int(kw.get("creationflags") or 0) | CREATE_NO_WINDOW
        if kw.get("startupinfo") is None:
            kw["startupinfo"] = _hidden_startupinfo()
    return kw


def _check_cmd(cmd: Sequence[Any]) -> list[str]:
    if isinstance(cmd, (str, bytes)):
        raise TypeError("Pass the command as a list of arguments, not a string")
    return [str(c) for c in cmd]


def popen(cmd: Sequence[Any], **kw) -> subprocess.Popen:
    """subprocess.Popen with no window, no shell, tracked for kill_all(). stdin defaults to /dev/null so a
    child can never wait for console input."""
    kw.setdefault("stdin", subprocess.DEVNULL)
    p = subprocess.Popen(_check_cmd(cmd), **hidden_kwargs(kw))
    with _lock:
        _children.add(p)
    return p


def run(cmd: Sequence[Any], *, check: bool = False, timeout: float | None = None, log_errors: bool = True,
        input: Any = None, capture_output: bool | None = None, **kw) -> subprocess.CompletedProcess:
    """subprocess.run with no window, no shell. Output is captured (unless the caller redirects it) and a
    failure's stderr goes to the app log instead of a console."""
    args = _check_cmd(cmd)
    if capture_output or ("stdout" not in kw and "stderr" not in kw):
        kw["stdout"] = subprocess.PIPE
        kw["stderr"] = subprocess.PIPE
    if input is not None:
        kw["stdin"] = subprocess.PIPE
    with popen(args, **kw) as p:
        try:
            out, err = p.communicate(input, timeout=timeout)
        except BaseException:               # timeout, Ctrl-C, ...: never leave the child running
            p.kill()
            p.communicate()
            raise
    res = subprocess.CompletedProcess(args, p.returncode, out, err)
    if res.returncode != 0 and log_errors:
        tail = err if isinstance(err, str) else (err or b"").decode("utf-8", "replace")
        log.warning("%s exited with %s: %s", os.path.basename(args[0]), res.returncode, (tail or "").strip()[-800:])
    if check:
        res.check_returncode()
    return res


def running() -> int:
    with _lock:
        return sum(1 for p in list(_children) if p.poll() is None)


def kill_all() -> int:
    """Stop every child process ClipFarm started (Pause engine, Quit)."""
    n = 0
    with _lock:
        kids = list(_children)
    for p in kids:
        try:
            if p.poll() is None:
                p.kill()
                n += 1
        except OSError:
            pass
    return n


def install_guard() -> None:
    """Make 'no window' the default for every subprocess in this process, including the ones libraries start
    themselves (yt-dlp runs ffmpeg). Windows only; idempotent."""
    global _guard_installed
    if not IS_WIN or _guard_installed:
        return
    orig = subprocess.Popen.__init__

    def _init(self, args, *a, **kw):        # noqa: ANN001
        if not a:                           # creationflags/startupinfo are keyword-only in practice
            kw["creationflags"] = int(kw.get("creationflags") or 0) | CREATE_NO_WINDOW
            if kw.get("startupinfo") is None:
                kw["startupinfo"] = _hidden_startupinfo()
        orig(self, args, *a, **kw)
        with _lock:
            _children.add(self)

    subprocess.Popen.__init__ = _init       # type: ignore[method-assign]
    _guard_installed = True


def kill_children_when_engine_dies() -> bool:
    """Windows: put this process in a Job Object with KILL_ON_JOB_CLOSE. Children started afterwards join
    the job, so if the engine exits or is killed (Quit, crash, Task Manager) Windows kills them too."""
    if not IS_WIN:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            return False
        if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(k32.GetCurrentProcess())):
            return False
        globals()["_job_handle"] = job          # keep the handle open for the life of the engine
        return True
    except Exception as e:                      # noqa: BLE001
        log.warning("Couldn't set up the child-process job object: %s", e)
        return False

