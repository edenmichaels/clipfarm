"""The Live Watcher (v0.8.9: chat-first). One background loop for the streamers you picked plus the ones auto-pilot
chose (permitted / campaign streamers only, Twitch).

Every 5 s while a streamer is live: chat tags (unique chatters, vs that channel's baseline) can open a trigger.
The trigger stays open until chat calms down or 10 s after its peak. Every 60 s: who is live (one Get Streams
request) and new viewer-made Twitch clips (also a trigger, and they teach us this streamer's chat delay).
A trigger is cut from the 3-minute buffer starting `delay` seconds (learned, default 20 s) before the chat spike,
transcribed with Whisper and judged by the local LLM from TEXT only (transcript + the chat). Judge >= threshold
(default 7) -> auto-edited clip in My Clips. Everything else is deleted. No video frames are analysed anywhere.
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
from .buffer import LiveBuffer
from .chat import DEFAULT_TAGS, DEFAULT_TRIGGER, ChatReader, ChatStats, evidence

log = logging.getLogger("clipfarm.live")

DEFAULTS = {"streamers": [], "auto_streamers": [], "paused": False, "threshold": 7.0, "per_hour": 6,
            "lead": 8, "tail": 5}
DEFAULT_DELAY = 20.0
TICK, POLL = 5.0, 60.0
COOLDOWN = 30.0


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


# ---- per-streamer settings: tags, trigger, learned delay ------------------------------------------------------------
def tags_for(sid: int) -> dict:
    t = db.get_setting(f"chat_tags:{sid}") or {}
    return {g: list(t.get(g) or DEFAULT_TAGS[g]) for g in DEFAULT_TAGS}


def trigger_for(sid: int) -> dict:
    return {**DEFAULT_TRIGGER, **(db.get_setting(f"chat_trigger:{sid}") or {})}


def delay_for(sid: int) -> float:
    d = db.get_setting(f"chat_delay:{sid}") or {}
    return float(d.get("delay", DEFAULT_DELAY))


def learn_delay(sid: int, clip_start: float, spikes: list[float]) -> float | None:
    """A viewer clip starts just before the moment; the chat spike after it tells us how late chat reacts."""
    after = [t for t in spikes if clip_start <= t <= clip_start + 60]
    if not after:
        return None
    sample = max(5.0, min(45.0, min(after) - clip_start))
    d = db.get_setting(f"chat_delay:{sid}") or {"delay": DEFAULT_DELAY, "samples": 0}
    if d.get("manual"):
        return float(d["delay"])                     # you set it by hand: keep it
    n = int(d.get("samples", 0))
    a = 1.0 if n == 0 else 0.3                       # first sample replaces the default, then a moving average
    new = round((1 - a) * float(d.get("delay", DEFAULT_DELAY)) + a * sample, 1)
    db.set_setting(f"chat_delay:{sid}", {"delay": new, "samples": n + 1, "last_sample": round(sample, 1)})
    return new


class Watch:
    """One streamer."""

    def __init__(self, streamer: dict, auto: bool = False):
        self.s = streamer
        self.auto = auto
        self.live = False
        self.stream: dict = {}
        self.stop = threading.Event()
        self.chat = self._new_chat()
        self.buffer: LiveBuffer | None = None
        self.triggers: deque[float] = deque()            # judged trigger times (for the hourly cap)
        self.last_trigger = 0.0
        self.episode: dict | None = None                 # an open chat trigger (until chat calms)
        self.spikes: list[float] = []
        self.seen_clips: set[str] = set()
        self.last_clip_poll = 0.0
        self.waiting: list[dict] = []                    # triggers waiting for the buffer to catch up
        self.session = {"since": time.time(), "triggers": 0, "kept": 0}

    def _new_chat(self) -> ChatStats:
        return ChatStats(tags_for(self.s["id"]), trigger_for(self.s["id"]), self.s.get("login"))

    # ---- live / offline ---------------------------------------------------------------------------------------
    def go_live(self, stream: dict) -> None:
        self.live, self.stream, self.stop = True, stream, threading.Event()
        self.chat = self._new_chat()
        ChatReader(self.s["login"], self.chat, self.stop).start()
        self.buffer = LiveBuffer(self.s["login"], folder(self.s["login"]) / "buffer", self.stop)
        self.buffer.start()
        self.last_clip_poll = time.time()
        self.session = {"since": time.time(), "triggers": 0, "kept": 0}
        db.log("info", "live", f"{self.s['display_name']} is live: reading chat and keeping a 3-minute buffer")

    def go_offline(self) -> None:
        if self.live:
            db.log("info", "live", f"{self.s['display_name']} went offline: buffer deleted")
        self.live = False
        self.stop.set()
        self.waiting.clear()
        self.episode = None

    # ---- triggers --------------------------------------------------------------------------------------------
    def per_hour_left(self, now: float, cap: int) -> int:
        while self.triggers and self.triggers[0] < now - 3600:
            self.triggers.popleft()
        return max(0, cap - len(self.triggers))

    def check(self, now: float, cfg: dict) -> dict | None:
        """Chat tags: open a trigger on a spike, close it once chat calms (or 10 s after the peak)."""
        st = self.chat.stats(now)
        ep = self.episode
        if ep is None:
            if not st["trigger"] or now - self.last_trigger < COOLDOWN:
                return None
            self.episode = {"spike": now - 5, "peak": now, "peak_w": st["weighted"], "strength": st["strength"]}
            self.spikes.append(now - 5)
            db.ex("INSERT INTO live_events(streamer_id, kind, t, data) VALUES (?, 'chat_spike', ?, ?)",
                  (self.s["id"], now - 5, json.dumps({k: st[k] for k in ("clip", "laugh", "shock", "unique", "ratio")})))
            return None
        if st["weighted"] > ep["peak_w"]:
            ep.update(peak=now, peak_w=st["weighted"], strength=max(ep["strength"], st["strength"]))
        calm = self.chat.calm_after(ep["peak"], now)
        end = min(calm if calm is not None else ep["peak"] + 10, ep["peak"] + 10)
        if now < end:
            return None
        self.episode = None
        delay = delay_for(self.s["id"])
        start = ep["spike"] - delay
        c = self.chat.counts(ep["spike"] - 5, end)
        return {"t": ep["spike"], "kind": "chat", "start": start, "end": end, "strength": ep["strength"],
                "evidence": evidence(c, end - ep["spike"] + 5), "counts": c, "delay": delay,
                "signals": {"chat": ep["strength"], "viewer_clips": 0, "clip": c["clip"], "laugh": c["laugh"],
                            "shock": c["shock"], "unique": c["unique"]}}

    def add_trigger(self, trig: dict, cfg: dict) -> bool:
        now = time.time()
        if self.per_hour_left(now, cfg["per_hour"]) <= 0 and trig["kind"] != "viewer_clip":
            db.log("info", "live", f"{self.s['display_name']}: skipped a trigger (hourly cap of {cfg['per_hour']})")
            return False
        self.last_trigger = now
        self.triggers.append(now)
        self.session["triggers"] += 1
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

    def cfg_for(self, w: Watch, cfg: dict) -> dict:
        """Auto-pilot's watchers use auto-pilot's min score and clips per hour."""
        if w.auto:
            from . import pilot
            ps = pilot.settings()
            return {**cfg, "threshold": float(ps["min_score"]), "per_hour": int(ps["clips_per_streamer_per_hour"])}
        return cfg

    def sync_streamers(self) -> None:
        """Watch list = the streamers you picked + the ones auto-pilot picked; only permitted Twitch streamers."""
        cfg = settings()
        mine = {int(x) for x in cfg["streamers"]}
        auto = {int(x) for x in cfg["auto_streamers"]}
        want = mine | auto
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
        for sid, w in self.watches.items():
            w.auto = sid in auto and sid not in mine

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
        if now - self.last_poll >= POLL:
            from . import pilot
            if pilot.settings()["enabled"] or cfg["auto_streamers"]:
                pilot.step(self, now)                    # auto-pilot picks who to watch (live, best A$ first)
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
            wc = self.cfg_for(w, cfg)
            if (trig := w.check(now, wc)):
                w.add_trigger(trig, wc)
            span = w.buffer.span() if w.buffer else None
            for trig in list(w.waiting):                 # judge once the buffer has the end of it
                if span and span[1] >= trig["end"] + cfg["tail"]:
                    w.waiting.remove(trig)
                    self.jobs.put((w, trig))
                elif now - trig["end"] > 120:
                    w.waiting.remove(trig)

    def poll(self, now: float, cfg: dict) -> None:
        """Who's live (one request) and new viewer clips (a trigger, and a lesson about chat delay)."""
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
            w.stream = live.get(uid) or w.stream
            since = iso(max(w.last_clip_poll - 90, now - 600))
            w.last_clip_poll = now
            for c in prov.recent_clips(uid, since):
                if c["id"] in w.seen_clips:
                    continue
                w.seen_clips.add(c["id"])
                made = epoch(c["created_at"])
                start = made - c["duration"]
                db.ex("INSERT INTO live_events(streamer_id, kind, t, data) VALUES (?, 'viewer_clip', ?, ?)",
                      (w.s["id"], made, json.dumps(c)))
                learn_delay(w.s["id"], start, w.spikes)
                cnt = w.chat.counts(start, made + delay_for(w.s["id"]))
                w.add_trigger({"t": start, "kind": "viewer_clip", "start": start - 3, "end": made + 2,
                               "strength": 1.0, "clip": c, "counts": cnt,
                               "evidence": f"a viewer clipped it ({c.get('title') or 'untitled'}); " +
                                           evidence(cnt, made + delay_for(w.s["id"]) - start),
                               "signals": {"chat": 0.5, "viewer_clips": 1, "clip": cnt["clip"], "laugh": cnt["laugh"],
                                           "shock": cnt["shock"], "unique": cnt["unique"]}}, self.cfg_for(w, cfg))

    # ---- judging (text only) ------------------------------------------------------------------------------------
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
        cfg = self.cfg_for(w, settings())
        t0, t1 = trig["start"] - cfg["lead"], trig["end"] + cfg["tail"]     # a little room for the judge's trim
        out = folder(w.s["login"]) / f"cut_{int(trig['t'])}.mp4"
        a, b = w.buffer.cut(t0, t1, out)
        length = b - a
        if transcribe is None:
            from ..highlights.transcribe import transcribe as _tr
            tc = db.get_setting("transcription", {}) or {}
            transcribe = lambda p: _tr(p, tc.get("model", "small"), tc.get("device", "auto"))   # noqa: E731
        words = transcribe(out)
        lag = trig.get("delay") or delay_for(w.s["id"])
        chat = [(t - a, m) for t, m in w.chat.window(a, b + lag)]          # chat lags: include its reaction
        res = J.judge(words, chat, length, w.s["display_name"], trig["signals"])
        if not res["llm"] or (res["start"], res["end"]) == (0, length):
            res["start"], res["end"] = max(0.0, trig["start"] - a), min(length, trig["end"] - a)
        nclips = trig["signals"].get("viewer_clips", 0)
        final = round(res["score"] + 2 * float(trig.get("strength", 0)) + min(2, nclips), 1)
        floor = cfg["threshold"] - (1 if trig["kind"] == "viewer_clip" else 0)
        keep = res["score"] >= floor
        quote = _quote(words, res["start"], res["end"])
        reason = f"{res['reason']} · {trig.get('evidence', '')}" + (f"; streamer: '{quote}'" if quote else "")
        mid = db.ex("""INSERT INTO live_moments(streamer_id, trigger, trigger_t, cut_start, cut_end, path, words, chat,
                       signals, judge_score, reason, best_start, best_end, moment_type, llm, status, viewer_clips,
                       chat_bonus, final_score, clip_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (w.s["id"], trig["kind"], trig["t"], a, b, str(out), json.dumps(words), json.dumps(chat[:300]),
                     json.dumps({**trig["signals"], "evidence": trig.get("evidence"), "delay": lag}), res["score"],
                     reason[:400], res["start"], res["end"], res["type"], int(res["llm"]),
                     "kept" if keep else "dropped", nclips, trig.get("strength", 0), final,
                     json.dumps(trig.get("clip")) if trig.get("clip") else None))
        if keep:
            w.session["kept"] += 1
            keep_moment(db.q1("SELECT * FROM live_moments WHERE id=?", (mid,)), final)
        else:
            out.unlink(missing_ok=True)                  # not a clip: gone with the buffer
        return {**res, "id": mid, "kept": keep, "final": final, "reason": reason}

    # ---- status for the panel -------------------------------------------------------------------------------------
    def status(self) -> dict:
        import psutil
        cfg = settings()
        try:
            cpu = psutil.Process().cpu_percent(interval=None) / max(1, psutil.cpu_count() or 1)
        except Exception:                                # noqa: BLE001
            cpu = None
        disk = 0
        try:
            root = folder("x").parent
            disk = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.exists() else 0
        except OSError:
            pass
        recent = db.q("""SELECT lm.id, lm.trigger, lm.judge_score, lm.final_score, lm.reason, lm.status, lm.created_at,
                         s.display_name FROM live_moments lm JOIN streamers s ON s.id=lm.streamer_id
                         ORDER BY lm.id DESC LIMIT 15""")
        from . import pilot
        return {"paused": cfg["paused"], "threshold": cfg["threshold"], "per_hour": cfg["per_hour"],
                "streamers": [r for r in rows(self) if r["watching"] or r["id"] in cfg["streamers"]],
                "cpu_percent": round(cpu, 1) if cpu is not None else None, "disk_bytes": disk, "recent": recent,
                "error": self.error, "autopilot": pilot.status(),
                "llm_warning": None if J.llm_ok() else "AI model isn't answering: moments are scored from chat and "
                                                       "viewer clips only (cautiously)."}


def _quote(words: list[dict], a: float, b: float) -> str:
    ws = [w["word"] for w in words if a <= float(w["start"]) <= b]
    txt = " ".join(ws).strip()
    return (txt[:70] + "…") if len(txt) > 70 else txt


def rows(m: Manager | None = None) -> list[dict]:
    """Every Twitch streamer you can watch, with what the Live Watcher tab sorts and filters on."""
    m = m or manager()
    cfg = settings()
    today = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    out = []
    for s in db.q("""SELECT s.id, s.display_name, s.login, s.platform, s.platform_user_id FROM streamers s
                     WHERE s.platform='twitch' ORDER BY s.display_name"""):
        ok, why = compliance.can_clip(s["id"])
        prog = db.q1("""SELECT p.name, p.payout_per_1k, p.campaign_type, p.joined FROM programs p
                        JOIN streamer_programs sp ON sp.program_id=p.id
                        WHERE sp.streamer_id=? AND p.active=1 ORDER BY p.joined DESC, p.payout_per_1k DESC LIMIT 1""",
                        (s["id"],))
        proj = db.q1("""SELECT MAX(per_day_likely) a FROM projections WHERE streamer_id=?""", (s["id"],)) \
            if _has_table("projections") else None
        w = m.watches.get(s["id"])
        trig = db.q1("SELECT COUNT(*) n, SUM(status='kept') k FROM live_moments WHERE streamer_id=?", (s["id"],))
        kept_today = db.q1("SELECT COUNT(*) n FROM live_moments WHERE streamer_id=? AND status='kept' AND created_epoch>=?",
                           (s["id"], today))["n"]
        out.append({**s, "allowed": ok, "why": None if ok else why, "watching": w is not None,
                    "auto": bool(w and w.auto), "live": bool(w and w.live),
                    "viewers": (w.stream.get("viewer_count") if w and w.live else None),
                    "game": (w.stream.get("game_name") if w and w.live else None),
                    "title": (w.stream.get("title") if w and w.live else None),
                    "campaign": prog["name"] if prog else None, "joined": bool(prog and prog["joined"]),
                    "rate": prog["payout_per_1k"] if prog else None,
                    "campaign_type": prog["campaign_type"] if prog else None,
                    "projected_aud": round(proj["a"], 2) if proj and proj["a"] is not None else None,
                    "chat_per_min": round(w.chat.rate_per_min()) if w and w.live else None,
                    "kept_today": kept_today, "triggers": trig["n"] or 0, "kept": trig["k"] or 0,
                    "hit_rate": round(100 * (trig["k"] or 0) / trig["n"]) if trig["n"] else None,
                    "triggers_hour": db.q1("SELECT COUNT(*) n FROM live_moments WHERE streamer_id=? AND created_epoch>=?",
                                           (s["id"], time.time() - 3600))["n"],
                    "delay": delay_for(s["id"]), "picked": s["id"] in cfg["streamers"],
                    "buffer_sec": round(w.buffer.span()[1] - w.buffer.span()[0]) if w and w.live and w.buffer
                    and w.buffer.span() else 0})
    return out


def _has_table(name: str) -> bool:
    return bool(db.q1("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


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
                (sid, m["best_start"], m["best_end"], min(1.0, final / 10), m["signals"], " ".join(w["word"] for w in words),
                 f"live:{s['id']}:{int(m['cut_start'] + m['best_start'])}", m.get("moment_type") or "Funny",
                 m.get("reason"), min(100, round(final * 10)), json.dumps(words)))
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
    from . import pilot
    if settings()["streamers"] or pilot.settings()["enabled"]:
        manager().start()
