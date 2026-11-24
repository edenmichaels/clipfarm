"""The Live Watcher: one background loop for the streamers you picked (permitted / campaign streamers only).

Every 5 s while a streamer is live: chat stats (Tier 0) and the buffer's laughter/scream scores (Tier 1) can fire
a trigger. Every 60 s: is anyone live (one Get Streams request) and new viewer clips (Get Clips). Triggers wait
until the buffer holds ~25 s after them, then a worker judges them one at a time (Tier 2, max ~6/hour per
streamer). Ten minutes later the judged moment is confirmed or dropped (Tier 3). Kept moments become normal
clips (auto-edit, render, My Clips); everything else is deleted with the buffer.
"""
from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .. import compliance, db
from . import judge as J
from .buffer import LiveBuffer, frames
from .chat import ChatReader, ChatStats

log = logging.getLogger("clipfarm.live")

DEFAULTS = {"streamers": [], "paused": False, "threshold": 7.0, "per_hour": 6, "trigger": 0.5,
            "cut_before": 55, "cut_after": 25, "confirm_after": 600}
TICK, POLL = 5.0, 60.0
COOLDOWN = 45.0


def settings() -> dict:
    return {**DEFAULTS, **(db.get_setting("live_watcher") or {})}


def save_settings(**kw) -> dict:
    s = {**settings(), **kw}
    db.set_setting("live_watcher", s)
    return s


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def folder(login: str) -> Path:
    from ..jobs import storage
    return storage.media_dir() / "live" / login


class Watch:
    """One streamer."""

    def __init__(self, streamer: dict):
        self.s = streamer
        self.live = False
        self.stream: dict = {}
        self.stop = threading.Event()
        self.chat = ChatStats()
        self.buffer: LiveBuffer | None = None
        self.triggers: deque[float] = deque()            # judged trigger times (for the hourly cap)
        self.last_trigger = 0.0
        self.seen_clips: set[str] = set()
        self.last_clip_poll = 0.0
        self.waiting: list[dict] = []                    # triggers waiting for the buffer to catch up

    # ---- live / offline ---------------------------------------------------------------------------------------
    def go_live(self, stream: dict) -> None:
        self.live, self.stream, self.stop = True, stream, threading.Event()
        self.chat = ChatStats()
        ChatReader(self.s["login"], self.chat, self.stop).start()
        self.buffer = LiveBuffer(self.s["login"], folder(self.s["login"]) / "buffer", self.stop)
        self.buffer.start()
        self.last_clip_poll = time.time()
        db.log("info", "live", f"{self.s['display_name']} is live: watching chat and keeping a 3-minute buffer")

    def go_offline(self) -> None:
        if self.live:
            db.log("info", "live", f"{self.s['display_name']} went offline: buffer deleted")
        self.live = False
        self.stop.set()
        self.waiting.clear()

    # ---- triggers --------------------------------------------------------------------------------------------
    def per_hour_left(self, now: float, cap: int) -> int:
        while self.triggers and self.triggers[0] < now - 3600:
            self.triggers.popleft()
        return max(0, cap - len(self.triggers))

    def check(self, now: float, cfg: dict) -> dict | None:
        """Tier 0 + 1: should we judge the last few seconds?"""
        st = self.chat.stats(now)
        chat = self.chat.score(st)
        audio = self.buffer.audio_peak(now - 20, now) if self.buffer else {}
        laugh, scream = audio.get("laughter", 0.0), audio.get("scream", 0.0)
        strength = max(chat, laugh * 0.9 if laugh >= 0.5 else 0, scream * 0.6 if scream >= 0.6 else 0,
                       0.5 * chat + 0.5 * laugh)
        if strength < cfg["trigger"] or now - self.last_trigger < COOLDOWN:
            return None
        kind = "chat" if chat >= max(laugh, scream) else "laughter" if laugh >= scream else "scream"
        return {"t": now - 8, "kind": kind, "strength": round(strength, 2), "chat": st,
                "signals": {"chat": chat, "laughter": round(laugh, 2), "scream": round(scream, 2), "viewer_clips": 0}}

    def add_trigger(self, trig: dict, cfg: dict) -> bool:
        now = time.time()
        if self.per_hour_left(now, cfg["per_hour"]) <= 0 and trig["kind"] != "viewer_clip":
            return False
        self.last_trigger = now
        self.triggers.append(now)
        self.waiting.append(trig)
        return True


class Manager:
    def __init__(self, provider_factory=None):
        self.watches: dict[int, Watch] = {}
        self.jobs: "queue.Queue[tuple[Watch, dict]]" = queue.Queue()
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.provider_factory = provider_factory
        self.last_poll = 0.0
        self.error: str | None = None
        self.started = time.time()

    # ---- lifecycle ----------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self.threads and any(t.is_alive() for t in self.threads):
            return
        self.stop.clear()
        self.threads = [threading.Thread(target=self._loop, daemon=True, name="live-watch"),
                        threading.Thread(target=self._worker, daemon=True, name="live-judge")]
        for t in self.threads:
            t.start()

    def shutdown(self) -> None:
        self.stop.set()
        for w in self.watches.values():
            w.go_offline()

    def provider(self):
        if self.provider_factory:
            return self.provider_factory()
        from ..providers.twitch import TwitchProvider
        return TwitchProvider()

    def sync_streamers(self) -> None:
        """Match the watch list to settings; only permitted Twitch streamers are watched."""
        want = set(int(x) for x in settings()["streamers"])
        for sid in list(self.watches):
            if sid not in want:
                self.watches.pop(sid).go_offline()
        for sid in want - set(self.watches):
            s = db.q1("SELECT * FROM streamers WHERE id=?", (sid,))
            ok, why = compliance.can_clip(sid) if s else (False, "unknown streamer")
            if not s or not ok or s["platform"] != "twitch":
                db.log("warn", "live", f"Not watching streamer {sid}: {why if s else 'not found'}"
                       + ("" if not s or s["platform"] == "twitch" else " (only Twitch streams can be watched live)"))
                continue
            self.watches[sid] = Watch(s)

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.step(time.time())
                self.error = None
            except Exception as e:                       # noqa: BLE001
                self.error = str(e)[:300]
                log.warning("live watcher: %s", e)
            self.stop.wait(TICK)

    def step(self, now: float) -> None:
        cfg = settings()
        self.sync_streamers()
        if cfg["paused"]:
            for w in self.watches.values():
                w.go_offline()
            return
        if now - self.last_poll >= POLL:
            self.last_poll = now
            self.poll(now, cfg)
        for w in self.watches.values():
            if not w.live:
                continue
            if (trig := w.check(now, cfg)):
                w.add_trigger(trig, cfg)
            span = w.buffer.span() if w.buffer else None
            for trig in list(w.waiting):                 # judge once the buffer has the aftermath
                if span and span[1] >= trig["t"] + min(cfg["cut_after"], trig.get("after", cfg["cut_after"])):
                    w.waiting.remove(trig)
                    self.jobs.put((w, trig))
                elif now - trig["t"] > 120:
                    w.waiting.remove(trig)
        self.confirm_due(now, cfg)

    def poll(self, now: float, cfg: dict) -> None:
        """Who's live (one request) and new viewer clips (Tier 0's strongest trigger)."""
        if not self.watches:
            return
        prov = self.provider()
        by_uid = {w.s["platform_user_id"]: w for w in self.watches.values()}
        live = prov.live_status(list(by_uid))
        for uid, w in by_uid.items():
            if uid in live and not w.live:
                w.go_live(live[uid])
            elif uid not in live and w.live:
                w.go_offline()
            if not w.live:
                continue
            since = iso(max(w.last_clip_poll - 90, now - 600))
            w.last_clip_poll = now
            for c in prov.recent_clips(uid, since):
                if c["id"] in w.seen_clips:
                    continue
                w.seen_clips.add(c["id"])
                made = epoch(c["created_at"])
                db.ex("INSERT INTO live_events(streamer_id, kind, t, data) VALUES (?, 'viewer_clip', ?, ?)",
                      (w.s["id"], made, json.dumps(c)))
                start, end = made - c["duration"] - 3, made + 2
                w.add_trigger({"t": end - 5, "kind": "viewer_clip", "strength": 1.0, "clip": c,
                               "window": (start - 10, end + 6), "after": 6,
                               "signals": {"chat": 0, "laughter": 0, "scream": 0, "viewer_clips": 1}}, cfg)

    # ---- Tier 2 ---------------------------------------------------------------------------------------------------
    def _worker(self) -> None:
        while not self.stop.is_set():
            try:
                w, trig = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.judge(w, trig)
            except Exception as e:                       # noqa: BLE001
                log.warning("live judge failed: %s", e)
                db.log("warn", "live", f"Couldn't judge a {trig['kind']} moment for {w.s['display_name']}: {e}")

    def judge(self, w: Watch, trig: dict, transcribe=None) -> dict:
        cfg = settings()
        t0, t1 = trig.get("window") or (trig["t"] - cfg["cut_before"], trig["t"] + cfg["cut_after"])
        t0, t1 = min(t0, t1 - 60), max(t1, t0 + 60)          # 60-90 s
        if t1 - t0 > 90:
            t0 = t1 - 90
        out = folder(w.s["login"]) / f"cut_{int(trig['t'])}.mp4"
        a, b = w.buffer.cut(t0, t1, out)
        length = b - a
        if transcribe is None:
            from ..highlights.transcribe import transcribe as _tr
            tc = db.get_setting("transcription", {}) or {}
            transcribe = lambda p: _tr(p, tc.get("model", "small"), tc.get("device", "auto"))   # noqa: E731
        words = transcribe(out)
        chat = [(t - a, m) for t, m in w.chat.window(a, b)]
        sig = dict(trig["signals"])
        audio = w.buffer.audio_peak(a, b)
        sig.update(laughter=max(sig.get("laughter", 0), audio["laughter"]), scream=max(sig.get("scream", 0), audio["scream"]))
        res = J.judge(words, chat, frames(out, length), length, w.s["display_name"], sig)
        mid = db.ex("""INSERT INTO live_moments(streamer_id, trigger, trigger_t, cut_start, cut_end, path, words, chat,
                       signals, judge_score, reason, best_start, best_end, moment_type, llm, status, confirm_at, clip_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (w.s["id"], trig["kind"], trig["t"], a, b, str(out), json.dumps(words), json.dumps(chat[:300]),
                     json.dumps(sig), res["score"], res["reason"], res["start"], res["end"], res["type"],
                     int(res["llm"]), "pending" if res["score"] >= cfg["threshold"] - 2 else "dropped",
                     time.time() + cfg["confirm_after"], json.dumps(trig.get("clip")) if trig.get("clip") else None))
        if res["score"] < cfg["threshold"] - 2:
            out.unlink(missing_ok=True)                  # clearly not a clip: gone with the buffer
        return {**res, "id": mid}

    # ---- Tier 3 ---------------------------------------------------------------------------------------------------
    def confirm_due(self, now: float, cfg: dict) -> None:
        for m in db.q("SELECT * FROM live_moments WHERE status='pending' AND confirm_at <= ?", (now,)):
            w = self.watches.get(m["streamer_id"])
            try:
                self.confirm(m, w, cfg)
            except Exception as e:                       # noqa: BLE001
                db.ex("UPDATE live_moments SET status='dropped', reason=reason || ? WHERE id=?",
                      (f" (confirm failed: {e})", m["id"]))
                Path(m["path"]).unlink(missing_ok=True)

    def confirm(self, m: dict, w: Watch | None, cfg: dict) -> dict:
        a, b = m["cut_start"], m["cut_end"]
        ms, me = a + m["best_start"], a + m["best_end"]
        clips = []
        if w is not None:
            try:
                clips = [c for c in self.provider().recent_clips(w.s["platform_user_id"], iso(a - 60))
                         if ms - 15 <= epoch(c["created_at"]) - c["duration"] <= me + 5 or
                         ms <= epoch(c["created_at"]) <= me + 60]
            except Exception:                            # noqa: BLE001
                clips = []
        for e in db.q("SELECT data FROM live_events WHERE streamer_id=? AND kind='viewer_clip' AND t BETWEEN ? AND ?",
                      (m["streamer_id"], ms, me + 60)):
            c = json.loads(e["data"])
            if c["id"] not in {x["id"] for x in clips}:
                clips.append(c)
        hype = 0.0
        if w is not None:
            after = w.chat.window(me, me + 180)
            before = max(1, len(w.chat.window(me - 600, me - 60)) / 540 * 180)
            hype = min(1.0, max(0.0, (len(after) / before - 1) / 3))
        bonus = min(2.0, 1.0 * len(clips)) + round(hype, 2)
        final = round(m["judge_score"] + bonus, 1)
        keep = final >= cfg["threshold"]
        db.ex("UPDATE live_moments SET viewer_clips=?, chat_bonus=?, final_score=?, status=? WHERE id=?",
              (len(clips), round(hype, 2), final, "kept" if keep else "dropped", m["id"]))
        if not keep:
            Path(m["path"]).unlink(missing_ok=True)
            return {"kept": False, "final": final}
        if clips:                                        # cut a better version around the viewer clip
            c = max(clips, key=lambda x: x.get("views", 0))
            made = epoch(c["created_at"])
            ms = max(a, made - c["duration"] - 8)        # a beat before the setup
            me = min(b, made + 4)                        # after the reaction
        cid = keep_moment({**m, "best_start": ms - a, "best_end": me - a}, final)
        return {"kept": True, "final": final, "clip_moment": cid}

    # ---- status for the panel -------------------------------------------------------------------------------------
    def status(self) -> dict:
        import psutil
        cfg = settings()
        p = psutil.Process()
        try:
            cpu = p.cpu_percent(interval=None) / max(1, psutil.cpu_count() or 1)
        except Exception:                                # noqa: BLE001
            cpu = None
        hour_ago = time.time() - 3600
        rows = []
        for sid in cfg["streamers"]:
            s = db.q1("SELECT id, display_name, login, platform FROM streamers WHERE id=?", (int(sid),))
            if not s:
                continue
            w = self.watches.get(int(sid))
            trig = db.q1("SELECT COUNT(*) n FROM live_moments WHERE streamer_id=? AND created_epoch >= ?", (s["id"], hour_ago))["n"]
            kept = db.q1("SELECT COUNT(*) n FROM live_moments WHERE streamer_id=? AND status='kept'", (s["id"],))["n"]
            rows.append({**s, "live": bool(w and w.live), "watching": w is not None,
                         "title": (w.stream.get("title") if w and w.live else None),
                         "triggers_hour": trig, "kept": kept,
                         "buffer_sec": round((lambda sp: sp[1] - sp[0] if sp else 0)(w.buffer.span() if w and w.buffer else None)),
                         "buffer_error": w.buffer.error if w and w.buffer and w.live else None})
        disk = 0
        try:
            root = folder("x").parent
            disk = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.exists() else 0
        except OSError:
            pass
        recent = db.q("""SELECT lm.id, lm.trigger, lm.judge_score, lm.final_score, lm.reason, lm.status, lm.created_at,
                         s.display_name FROM live_moments lm JOIN streamers s ON s.id=lm.streamer_id
                         ORDER BY lm.id DESC LIMIT 15""")
        return {"paused": cfg["paused"], "threshold": cfg["threshold"], "per_hour": cfg["per_hour"],
                "streamers": rows, "cpu_percent": round(cpu, 1) if cpu is not None else None,
                "disk_bytes": disk, "recent": recent, "error": self.error,
                "llm_warning": None if J.llm_ok() else "AI model isn't answering: moments are scored from chat, "
                                                       "laughter and viewer clips only (cautiously)."}


def keep_moment(m: dict, final: float) -> int:
    """A confirmed moment becomes a normal moment + render job (auto-edit, captions, hook) -> My Clips."""
    from ..jobs.engine import enqueue
    s = db.q1("SELECT * FROM streamers WHERE id=?", (m["streamer_id"],))
    compliance.assert_can_clip(s["id"])
    src = Path(m["path"])
    keep = folder(s["login"]) / "kept" / src.name
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), keep)
    pid = live_project(s)
    sid = db.ex("""INSERT INTO sources(streamer_id, kind, platform_id, url, title, duration_sec, created_at_platform,
                   local_path, status) VALUES (?, 'live', ?, ?, ?, ?, ?, ?, 'ready')""",
                (s["id"], f"live-{m['id']}", f"https://www.twitch.tv/{s['login']}", m.get("reason") or "Live moment",
                 m["cut_end"] - m["cut_start"], iso(m["cut_start"]), str(keep)))
    words = [w for w in json.loads(m["words"] or "[]") if m["best_start"] - 1 <= w["start"] <= m["best_end"] + 1]
    mid = db.ex("""INSERT INTO moments(source_id, start_sec, end_sec, score, signals, transcript, fingerprint, status,
                   type, reason, viral_score, words) VALUES (?,?,?,?,?,?,?, 'queued', ?, ?, ?, ?)""",
                (sid, m["best_start"], m["best_end"], final / 10, m["signals"], " ".join(w["word"] for w in words),
                 f"live:{s['id']}:{int(m['cut_start'] + m['best_start'])}", m.get("moment_type") or "Funny",
                 m.get("reason"), round(final * 10), json.dumps(words)))
    final_path = keep.with_name(f"src{sid}_m{mid}.mp4")       # render_moment moves this into sources/ (no copy left)
    keep.rename(final_path)
    keep = final_path
    db.ex("UPDATE sources SET local_path=? WHERE id=?", (str(keep), sid))
    db.ex("UPDATE live_moments SET moment_id=?, path=? WHERE id=?", (mid, str(keep), m["id"]))
    enqueue("render_moment", {"project_id": pid, "moment_id": mid, "media": str(keep), "media_offset": 0.0,
                              "payoff": max(1.0, (m["best_end"] - m["best_start"]) * 0.6)}, pid, priority=2)
    db.log("info", "live", f"Kept a {s['display_name']} moment ({final}/10): {m.get('reason')}")
    return mid


def live_project(s: dict) -> int:
    """The (hidden) project that holds a streamer's Live Watcher clips."""
    projects = settings().get("projects") or {}
    pid = projects.get(str(s["id"]))
    if pid and db.q1("SELECT 1 FROM projects WHERE id=?", (pid,)):
        return pid
    from ..campaigns import autofill
    filled = autofill.fill([s["id"]], None)
    out = autofill.output_settings({}, filled)
    game = db.q1("SELECT id FROM games WHERE name LIKE '%Grand Theft Auto%' OR name LIKE 'GTA%' ORDER BY id LIMIT 1")
    pid = db.ex("""INSERT INTO projects(name, game_id, status, duration_sec, output_settings, started_at)
                   VALUES (?,?,'running',0,?,datetime('now'))""",
                (f"Live: {s['display_name']}", game["id"] if game else None, json.dumps(out)))
    db.ex("INSERT OR IGNORE INTO project_streamers(project_id, streamer_id) VALUES (?,?)", (pid, s["id"]))
    save_settings(projects={**projects, str(s["id"]): pid})
    return pid


_manager: Manager | None = None


def manager() -> Manager:
    global _manager
    if _manager is None:
        _manager = Manager()
    return _manager


def start_if_configured() -> None:
    if settings()["streamers"]:
        manager().start()
