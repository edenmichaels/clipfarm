"""v0.7.1 stability: no console windows for child processes, the short-source crash, bounded retries,
engine pause, and only one engine per data folder."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from clipfarm import db, proc
from clipfarm.highlights import detect, signals

PKG = Path(__file__).resolve().parents[1] / "clipfarm"


# ---- 1. child processes: one helper, hidden windows, no shell ---------------------------------------
def test_every_external_program_goes_through_the_helper():
    """No subprocess.run/Popen/call/check_output, os.system, os.popen or shell=True anywhere but proc.py."""
    bad = []
    for f in PKG.rglob("*.py"):
        if f.name == "proc.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = ""
                if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                    name = f"{fn.value.id}.{fn.attr}"
                if name in ("subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output",
                            "subprocess.check_call", "os.system", "os.popen", "os.startfile", "os.spawnl",
                            "os.execv"):
                    bad.append(f"{f.relative_to(PKG)}:{node.lineno} {name}")
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value:
                        bad.append(f"{f.relative_to(PKG)}:{node.lineno} shell=True")
    assert not bad, "Start programs with clipfarm.proc.run/popen:\n" + "\n".join(bad)


class _SI:      # stand-in for subprocess.STARTUPINFO on non-Windows test machines
    def __init__(self):
        self.dwFlags = 0
        self.wShowWindow = 1


def test_windows_flags_hide_every_child(monkeypatch):
    monkeypatch.setattr(proc, "IS_WIN", True)
    monkeypatch.setattr(subprocess, "STARTUPINFO", _SI, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    kw = proc.hidden_kwargs({})
    assert kw["creationflags"] & 0x08000000                        # CREATE_NO_WINDOW
    si = kw["startupinfo"]
    assert si.dwFlags & 1 and si.wShowWindow == 0                  # STARTF_USESHOWWINDOW + SW_HIDE
    assert proc.hidden_kwargs({"creationflags": 0x200})["creationflags"] == 0x200 | 0x08000000
    with pytest.raises(ValueError):
        proc.hidden_kwargs({"shell": True})


def test_helper_rejects_command_strings_and_logs_failures(caplog):
    with pytest.raises(TypeError):
        proc.run("ffmpeg -version")
    r = proc.run(["ffmpeg", "-v", "error", "-i", "/no/such/file.mp4", "-f", "null", "-"])
    assert r.returncode != 0 and b"No such file" in r.stderr
    assert any("ffmpeg exited" in m for m in caplog.messages)
    assert proc.run([sys.executable, "-c", "import sys; print(sys.stdin.read())"], input=b"hi").stdout.strip() == b"hi"


def test_kill_all_stops_running_children():
    p = proc.popen([sys.executable, "-c", "import time; time.sleep(30)"])
    assert proc.running() >= 1
    assert proc.kill_all() >= 1
    p.wait(timeout=5)
    assert p.returncode is not None


def test_timeout_never_leaves_a_child_running():
    with pytest.raises(subprocess.TimeoutExpired):
        proc.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    time.sleep(0.2)
    assert proc.running() == 0


# ---- 2. the process_source crash ---------------------------------------------------------------------
def _events(n):
    return {k: np.full(n, 0.2, np.float32) for k in ("laughter", "speech", "music", "silence", "scream", "cheer",
                                                     "explosion")}


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7, 8])
def test_short_source_no_longer_crashes(n):
    """Reproduces "setting an array element with a sequence ... detected shape was (3,) + inhomogeneous part":
    a source shorter than the 9 s smoothing window (short Twitch clips) made one signal longer than the rest."""
    ch = signals.chat_reactions([(0.5, "KEKW")] * 3, n)
    sig = detect.Signals(n, events=_events(n), loudness=np.full(n, -20.0, np.float32), chat=ch,
                         clips=[(0.0, 100)])
    comp, parts = detect.components(sig)
    assert all(v.shape == (n,) for v in comp.values()) and all(np.shape(v) == (n,) for v in parts.values())
    cv = detect.curve(comp, parts)
    assert cv.shape == (n,)
    assert detect.pick(cv, parts, comp, np.full(n, -20.0, np.float32), budget=1, min_len=1, max_len=60,
                       threshold=0.0) is not None


def test_smooth_keeps_length_and_matches_old_results_for_normal_input():
    rng = np.random.default_rng(1)
    for n in (0, 1, 2, 5, 9, 50):
        for k in (2, 3, 5, 9, 15):
            x = rng.random(n).astype(np.float32)
            y = signals.smooth(x, k)
            assert y.shape == (n,)
            if n >= k:
                assert np.allclose(y, np.convolve(x, np.ones(k) / k, mode="same"), atol=1e-6)


def test_detect_source_on_a_4_second_clip(tmp_path):
    from clipfarm.highlights import run
    f = tmp_path / "short.mp4"
    proc.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=4", "-f", "lavfi",
              "-i", "sine=f=440:d=4", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(f)],
             check=True)
    ms, dbg = run.detect_source(f, budget=1, min_len=1, max_len=60, threshold=0.0)
    assert dbg["duration"] in (4, 5)


# ---- 3. bounded retries ------------------------------------------------------------------------------
def _engine_with_failing_handler(monkeypatch, kind="process_source"):
    from clipfarm.jobs import engine, pipeline  # noqa: F401  (pipeline registers the failure hooks)
    calls = []

    def boom(payload, ctx):
        calls.append(1)
        raise ValueError("setting an array element with a sequence")
    monkeypatch.setitem(engine.HANDLERS, kind, boom)
    return engine, calls


def _run_due(engine, jid):
    db.ex("UPDATE jobs SET run_after=datetime('now') WHERE id=? AND status='queued'", (jid,))
    job = engine.Engine()._claim("1=1")
    assert job and job["id"] == jid
    engine.Engine()._run(job)
    return db.q1("SELECT * FROM jobs WHERE id=?", (jid,))


def test_failed_source_retries_three_times_then_shows_failed(fresh_db, monkeypatch):
    engine, calls = _engine_with_failing_handler(monkeypatch)
    sid = db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, permission_status)
                   VALUES ('twitch','1','s','S','allowed')""")
    src = db.ex("INSERT INTO sources(streamer_id, kind, platform_id, url) VALUES (?,?,?,?)",
                (sid, "featured_clip", "abc", "https://clips.twitch.tv/abc"))
    jid = engine.enqueue("process_source", {"project_id": None, "source_id": src})
    gaps = []
    for _ in range(3):
        j = _run_due(engine, jid)
        assert j["status"] == "queued"
        gaps.append(db.q1("SELECT CAST(strftime('%s', run_after) - strftime('%s','now') AS INT) g FROM jobs WHERE id=?",
                          (jid,))["g"])
        s = db.q1("SELECT status, error, retry_at FROM sources WHERE id=?", (src,))
        assert s["status"] == "new" and "array element" in s["error"] and s["retry_at"]
    assert [round(g / 10) * 10 for g in gaps] == [30, 120, 600]
    j = _run_due(engine, jid)
    assert j["status"] == "failed" and len(calls) == 4
    s = db.q1("SELECT status, error FROM sources WHERE id=?", (src,))
    assert s["status"] == "error" and "array element" in s["error"]
    assert engine.Engine()._claim("1=1") is None                    # nothing left to loop on


def test_permanent_errors_are_not_retried(fresh_db, monkeypatch):
    from clipfarm.jobs import engine
    from clipfarm.jobs.pipeline import PermanentError

    def nope(payload, ctx):
        raise PermanentError("Kick has no VODs")
    monkeypatch.setitem(engine.HANDLERS, "fetch_sources", nope)
    jid = engine.enqueue("fetch_sources", {})
    assert _run_due(engine, jid)["status"] == "failed"


def test_paused_engine_starts_nothing_and_requeues_interrupted_jobs(fresh_db, monkeypatch):
    from clipfarm.jobs import engine
    started = []

    def slow(payload, ctx):
        started.append(1)
        engine.pause_engine()                    # pause while this job runs: its ffmpeg would be killed
        raise RuntimeError("ffmpeg was stopped")
    monkeypatch.setitem(engine.HANDLERS, "process_source", slow)
    try:
        jid = engine.enqueue("process_source", {"source_id": 0})
        j = _run_due(engine, jid)
        assert j["status"] == "queued" and j["attempts"] == 0          # retry not used up
        assert engine.is_paused() and db.get_setting("engine_paused") is True
        e = engine.Engine()
        e.start()
        time.sleep(1.5)
        assert started == [1]                                           # nothing claimed while paused
        engine.resume_engine()
        assert not engine.is_paused()
        e.stop()
    finally:
        engine.PAUSED.clear()


def test_engine_loop_starts_once(fresh_db):
    from clipfarm.jobs import engine
    e = engine.Engine(workers=1)
    e.start()
    n = len(e.threads)
    e.start()
    assert len(e.threads) == n
    e.stop()


# ---- 5. one engine per data folder -------------------------------------------------------------------
def test_only_one_engine_can_hold_the_lock(tmp_path):
    lock = tmp_path / "engine.lock"
    code = ("import sys; sys.path.insert(0, %r); from clipfarm import main; "
            "print(main.acquire_single_instance(__import__('pathlib').Path(%r), wait_sec=0.5), flush=True)")
    holder = proc.popen([sys.executable, "-c", code % (str(PKG.parent), str(lock)) + "; import time; time.sleep(20)"],
                        stdout=subprocess.PIPE)
    assert holder.stdout.readline().strip() == b"True"
    try:
        second = proc.run([sys.executable, "-c", code % (str(PKG.parent), str(lock))], timeout=20)
        assert second.stdout.strip() == b"False"
    finally:
        holder.kill()
        holder.wait()
    third = proc.run([sys.executable, "-c", code % (str(PKG.parent), str(lock))], timeout=20)
    assert third.stdout.strip() == b"True"          # released when the holder dies


def test_failed_sources_and_engine_routes_exist():
    src = (PKG / "api.py").read_text(encoding="utf-8")
    for route in ('"/engine/status"', '"/engine/pause"', '"/engine/resume"', '"/engine/shutdown"',
                  '"/sources/problems"', '"/sources/{sid}/retry"'):
        assert route in src
    assert json.loads('{"ok": true}')
