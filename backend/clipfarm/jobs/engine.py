"""Durable background job engine.

- Jobs live in SQLite, so a crash/reboot loses nothing: on start, 'running' jobs go back to 'queued'.
- N worker threads claim jobs atomically (UPDATE ... WHERE status='queued' RETURNING).
- Failed jobs retry with exponential backoff up to max_attempts.
- Projects are driven by a self-rescheduling 'project_tick' job until ends_at.
- Events (clip ready, error, etc.) go to an in-memory queue the API streams to the UI (SSE),
  which turns them into desktop toasts.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from typing import Callable

from .. import db

HANDLERS: dict[str, Callable[[dict, "JobContext"], None]] = {}
EVENTS: "queue.Queue[dict]" = queue.Queue(maxsize=1000)


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def emit(kind: str, **data) -> None:
    try:
        EVENTS.put_nowait({"type": kind, "ts": time.time(), **data})
    except queue.Full:
        pass


def enqueue(kind: str, payload: dict, project_id: int | None = None, priority: int = 5,
            delay_sec: int = 0) -> int:
    return db.ex("""INSERT INTO jobs(project_id, kind, payload, priority, run_after)
                    VALUES (?,?,?,?, datetime('now', ?))""",
                 (project_id, kind, json.dumps(payload), priority, f"+{int(delay_sec)} seconds"))


class Cancelled(Exception):
    pass


class JobContext:
    def __init__(self, job: dict):
        self.job = job
        self.id = job["id"]

    def progress(self, frac: float, eta_sec: int | None = None) -> None:
        db.ex("UPDATE jobs SET progress=?, eta_sec=?, updated_at=datetime('now') WHERE id=?",
              (max(0.0, min(1.0, frac)), eta_sec, self.id))
        status = db.q1("SELECT status FROM jobs WHERE id=?", (self.id,))["status"]
        if status in ("cancelled", "paused"):
            raise Cancelled(status)

    def log(self, msg: str, level: str = "info") -> None:
        db.log(level, self.job["kind"], msg, self.id)


class Engine:
    def __init__(self, workers: int = 2, render_workers: int = 1):
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        # Rendering is GPU/CPU heavy: dedicated lane(s) so downloads/detection keep flowing.
        self.lanes = [("general", workers, "kind NOT IN ('render')"), ("render", render_workers, "kind='render'")]

    def start(self) -> None:
        recovered = db.connect().execute(
            "UPDATE jobs SET status='queued', updated_at=datetime('now') WHERE status='running'").rowcount
        if recovered:
            db.log("info", "engine", f"Recovered {recovered} interrupted job(s) after restart")
        for lane, n, where in self.lanes:
            for i in range(n):
                t = threading.Thread(target=self._loop, args=(where,), daemon=True, name=f"{lane}-{i}")
                t.start()
                self.threads.append(t)

    def stop(self) -> None:
        self.stop_event.set()

    def _claim(self, where: str) -> dict | None:
        row = db.connect().execute(f"""
            UPDATE jobs SET status='running', attempts=attempts+1, updated_at=datetime('now')
            WHERE id = (SELECT id FROM jobs WHERE status='queued' AND run_after <= datetime('now') AND {where}
                        AND (project_id IS NULL OR project_id NOT IN
                             (SELECT id FROM projects WHERE status IN ('paused','stopped')))
                        ORDER BY priority, id LIMIT 1)
            RETURNING *""").fetchone()
        return dict(row) if row else None

    def _loop(self, where: str) -> None:
        while not self.stop_event.is_set():
            try:
                job = self._claim(where)
                if not job:
                    self.stop_event.wait(1.0)
                    continue
                self._run(job)
            except Exception as e:      # a worker must never die (it used to on a failing job's error event)
                try:
                    db.log("error", "engine", f"Worker error: {e}")
                except Exception:
                    pass
                self.stop_event.wait(2.0)

    def _run(self, job: dict) -> None:
        ctx = JobContext(job)
        fn = HANDLERS.get(job["kind"])
        try:
            if not fn:
                raise RuntimeError(f"No handler for job kind {job['kind']}")
            fn(json.loads(job["payload"]), ctx)
            db.ex("UPDATE jobs SET status='done', progress=1, updated_at=datetime('now') WHERE id=?", (job["id"],))
        except Cancelled as c:
            # paused -> requeue later; cancelled -> stays cancelled
            if str(c) == "paused":
                db.ex("UPDATE jobs SET status='paused' WHERE id=?", (job["id"],))
        except Exception as e:
            from ..redact import redact
            err = redact(f"{e}\n{traceback.format_exc(limit=3)}")
            retry = job["attempts"] < job["max_attempts"] and not getattr(e, "permanent", False)
            if retry:
                delay = 30 * 2 ** (job["attempts"] - 1)
                db.ex("""UPDATE jobs SET status='queued', error=?, run_after=datetime('now', ?) WHERE id=?""",
                      (err, f"+{delay} seconds", job["id"]))
            else:
                db.ex("UPDATE jobs SET status='failed', error=? WHERE id=?", (err, job["id"]))
                emit("error", job_id=job["id"], job_kind=job["kind"], message=redact(str(e)))
            db.log("error", job["kind"], str(e), job["id"])


# ---- project lifecycle -----------------------------------------------------
def start_project(project_id: int) -> None:
    p = db.q1("SELECT * FROM projects WHERE id=?", (project_id,))
    db.ex("""UPDATE projects SET status='running', started_at=COALESCE(started_at, datetime('now')),
             ends_at=COALESCE(ends_at, datetime('now', ?)) WHERE id=?""",
          (f"+{p['duration_sec']} seconds", project_id))
    enqueue("project_tick", {"project_id": project_id}, project_id, priority=1)
    emit("project", project_id=project_id, status="running")


def pause_project(project_id: int) -> None:
    db.ex("UPDATE projects SET status='paused' WHERE id=?", (project_id,))
    db.ex("UPDATE jobs SET status='paused' WHERE project_id=? AND status IN ('queued','running')", (project_id,))
    emit("project", project_id=project_id, status="paused")


def resume_project(project_id: int) -> None:
    db.ex("UPDATE projects SET status='running' WHERE id=?", (project_id,))
    db.ex("UPDATE jobs SET status='queued' WHERE project_id=? AND status='paused'", (project_id,))
    if not db.q1("SELECT 1 FROM jobs WHERE project_id=? AND kind='project_tick' AND status='queued'", (project_id,)):
        enqueue("project_tick", {"project_id": project_id}, project_id, priority=1)
    emit("project", project_id=project_id, status="running")


def stop_project(project_id: int) -> None:
    db.ex("UPDATE projects SET status='stopped' WHERE id=?", (project_id,))
    db.ex("UPDATE jobs SET status='cancelled' WHERE project_id=? AND status IN ('queued','paused','running')",
          (project_id,))
    emit("project", project_id=project_id, status="stopped")
