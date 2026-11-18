"""Job handlers: the MVP pipeline.

project_tick -> fetch_sources (per streamer) -> process_source -> render_moment -> (review) -> export/post
Each handler is idempotent where possible so retries after a crash are safe.
"""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import compliance, db
from ..discovery.service import rank_game
from ..editing.hooks import generate_package
from ..editing.layout import Box
from ..editing.render import RenderSpec, render
from ..highlights import dedupe, scorer, signals
from ..highlights.chat_logger import ChatLogger, load_chat
from ..highlights.transcribe import speech_excitement, transcribe
from ..providers import available_providers
from . import media, storage
from .engine import JobContext, emit, enqueue, handler

TICK_MINUTES = 10
_chat_loggers: dict[str, threading.Event] = {}


class PermanentError(RuntimeError):
    permanent = True


def _project(pid: int) -> dict:
    p = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
    p["settings"] = json.loads(p["output_settings"])
    return p


def _clip_cfg(project: dict) -> dict:
    cfg = dict(db.get_setting("clip", {}))
    cfg.update(project["settings"].get("clip", {}))
    return cfg


@handler("project_tick")
def project_tick(payload: dict, ctx: JobContext) -> None:
    pid = payload["project_id"]
    p = _project(pid)
    if p["status"] != "running":
        return
    if p["ends_at"] and datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") >= p["ends_at"]:
        db.ex("UPDATE projects SET status='completed' WHERE id=?", (pid,))
        for ev in _chat_loggers.values():
            ev.set()
        emit("project", project_id=pid, status="completed")
        return
    streamers = db.q("""SELECT s.* FROM streamers s JOIN project_streamers ps ON ps.streamer_id=s.id
                        WHERE ps.project_id=?""", (pid,))
    for s in streamers:
        ok, reason = compliance.can_clip(s["id"])
        if not ok:
            ctx.log(f"Skipping {s['display_name']}: {reason}", "warn")
            continue
        enqueue("fetch_sources", {"project_id": pid, "streamer_id": s["id"]}, pid, priority=2)
    if p["game_id"]:
        rank_game(p["game_id"])
    enqueue("cleanup", {}, pid, priority=9)
    enqueue("project_tick", {"project_id": pid}, pid, priority=1, delay_sec=TICK_MINUTES * 60)


@handler("fetch_sources")
def fetch_sources(payload: dict, ctx: JobContext) -> None:
    pid, sid = payload["project_id"], payload["streamer_id"]
    compliance.assert_can_clip(sid)
    s = db.q1("SELECT * FROM streamers WHERE id=?", (sid,))
    prov = available_providers().get(s["platform"])
    if not prov:
        raise PermanentError(f"{s['platform']} is not connected")
    hits = []
    if s["platform"] == "twitch":
        hits += prov.clips(s["platform_user_id"], days=2, featured=True, n=20)
        hits += prov.clips(s["platform_user_id"], days=2, featured=False, n=30)
        hits += prov.vods(s["platform_user_id"], n=2)
        _ensure_chat_logger(s)
    elif hasattr(prov, "vods"):
        hits += prov.vods(s["platform_user_id"], n=2)
    new = 0
    for h in hits:
        exists = db.q1("SELECT id FROM sources WHERE streamer_id=? AND kind=? AND platform_id=?",
                       (sid, h.kind, h.platform_id))
        if exists:
            continue
        src_id = db.ex("""INSERT INTO sources(streamer_id, kind, platform_id, url, title, view_count, duration_sec,
                          created_at_platform) VALUES (?,?,?,?,?,?,?,?)""",
                       (sid, h.kind, h.platform_id, h.url, h.title, h.view_count, h.duration_sec, h.created_at))
        prio = {"featured_clip": 3, "community_clip": 4, "vod": 6}[h.kind]
        enqueue("process_source", {"project_id": pid, "source_id": src_id,
                                   "vod_offset": h.vod_offset_sec}, pid, priority=prio)
        new += 1
    ctx.log(f"{s['display_name']}: {new} new source(s)")


def _ensure_chat_logger(s: dict) -> None:
    """Log live chat for Twitch streamers so VOD analysis gets a chat signal."""
    key = s["login"]
    if key in _chat_loggers and not _chat_loggers[key].is_set():
        return
    ev = threading.Event()
    out = storage.media_dir() / "chat" / f"{key}-{datetime.now():%Y%m%d}.jsonl"
    ChatLogger(key, out, ev).start()
    _chat_loggers[key] = ev


@handler("process_source")
def process_source(payload: dict, ctx: JobContext) -> None:
    pid, src_id = payload["project_id"], payload["source_id"]
    src = db.q1("SELECT * FROM sources WHERE id=?", (src_id,))
    compliance.assert_can_clip(src["streamer_id"])
    p = _project(pid)
    cfg = _clip_cfg(p)
    work = storage.media_dir() / "sources" / str(src["streamer_id"])
    db.ex("UPDATE sources SET status='downloading' WHERE id=?", (src_id,))

    if src["kind"] != "vod":
        # --- clip: already a highlight; trim to best window and render --------------------
        path = media.download_clip(src["url"], work, f"src{src_id}")
        db.ex("UPDATE sources SET status='ready', local_path=?, bytes=? WHERE id=?",
              (str(path), path.stat().st_size, src_id))
        ctx.progress(0.5)
        audio = signals.audio_rms(path)
        curve, normed = scorer.composite({"audio": audio})
        dur = float(len(audio))
        if dur <= cfg["max_sec"]:
            start, end = 0.0, dur
        else:
            ms = scorer.pick_moments(curve, normed, min_len=cfg["min_sec"], max_len=cfg["max_sec"],
                                     threshold=0.0, top_n_per_hour=1)
            start, end = (ms[0].start, ms[0].end) if ms else (0.0, cfg["max_sec"])
        views = src["view_count"] or 0
        score = round(min(1.0, 0.5 + np.log10(views + 1) / 10), 3)  # community already voted
        fp = dedupe.fingerprint(src["streamer_id"], src["created_at_platform"], 0, src["platform_id"])
        status = "duplicate" if dedupe.is_duplicate(fp) else "queued"
        mid = db.ex("""INSERT INTO moments(source_id, start_sec, end_sec, score, signals, fingerprint, status)
                       VALUES (?,?,?,?,?,?,?)""",
                    (src_id, start, end, score, json.dumps({"clip_views": views}), fp, status))
        if status == "queued":
            enqueue("render_moment", {"project_id": pid, "moment_id": mid, "media": str(path),
                                      "media_offset": 0.0}, pid, priority=3)
        else:
            db.ex("UPDATE sources SET status='processed' WHERE id=?", (src_id,))
        return

    # --- VOD: audio-only analysis, then fetch just the winning sections -------------------
    audio_path = media.download_audio(src["url"], work, f"src{src_id}")
    db.ex("UPDATE sources SET status='ready', local_path=? WHERE id=?", (str(audio_path), src_id))
    ctx.progress(0.3)
    audio = signals.audio_rms(audio_path)
    n = len(audio)
    vod_start = datetime.fromisoformat(src["created_at_platform"].replace("Z", "+00:00")).timestamp() \
        if src["created_at_platform"] else 0
    chat = []
    login = db.q1("SELECT login FROM streamers WHERE id=?", (src["streamer_id"],))["login"]
    for f in (storage.media_dir() / "chat").glob(f"{login}-*.jsonl"):
        chat += load_chat(f, vod_start)
    rate, burst = signals.chat_series(chat, n)
    curve, normed = scorer.composite({"audio": audio, "chat": rate, "emote": burst})
    moments = scorer.pick_moments(curve, normed, min_len=cfg["min_sec"], max_len=cfg["max_sec"],
                                  pad_before=cfg["pad_before"], pad_after=cfg["pad_after"],
                                  threshold=cfg["score_threshold"], top_n_per_hour=cfg["top_n_per_hour"])
    # Community clips that fall inside this VOD boost nearby moments.
    clip_offsets = []
    for c in db.q("""SELECT view_count, created_at_platform FROM sources WHERE streamer_id=?
                     AND kind!='vod' AND created_at_platform IS NOT NULL""", (src["streamer_id"],)):
        off = datetime.fromisoformat(c["created_at_platform"].replace("Z", "+00:00")).timestamp() - vod_start
        if 0 <= off <= n:
            clip_offsets.append((off, c["view_count"] or 0))
    scorer.boost_with_clip_views(moments, clip_offsets)
    ctx.log(f"VOD {src['platform_id']}: {len(moments)} moment(s) above threshold")
    for i, m in enumerate(moments):
        fp = dedupe.fingerprint(src["streamer_id"], src["created_at_platform"], m.peak, src["platform_id"])
        if dedupe.is_duplicate(fp):
            continue
        mid = db.ex("""INSERT INTO moments(source_id, start_sec, end_sec, score, signals, fingerprint, status)
                       VALUES (?,?,?,?,?,?, 'queued')""",
                    (src_id, m.start, m.end, m.score, json.dumps(m.signals), fp))
        section = media.download_section(src["url"], work, f"src{src_id}_m{mid}", m.start, m.end)
        enqueue("render_moment", {"project_id": pid, "moment_id": mid, "media": str(section),
                                  "media_offset": m.start}, pid, priority=4)
        ctx.progress(0.3 + 0.7 * (i + 1) / max(1, len(moments)))
    Path(audio_path).unlink(missing_ok=True)   # audio no longer needed
    if not moments:
        db.ex("UPDATE sources SET status='processed' WHERE id=?", (src_id,))


@handler("render_moment")
def render_moment(payload: dict, ctx: JobContext) -> None:
    pid, mid = payload["project_id"], payload["moment_id"]
    m = db.q1("""SELECT m.*, s.streamer_id, s.id AS src_id FROM moments m JOIN sources s ON s.id=m.source_id
                 WHERE m.id=?""", (mid,))
    compliance.assert_can_clip(m["streamer_id"])
    if db.q1("SELECT id FROM clips WHERE moment_id=?", (mid,)):
        return  # already rendered (retry after crash)
    p = _project(pid)
    settings = p["settings"]
    streamer = db.q1("SELECT * FROM streamers WHERE id=?", (m["streamer_id"],))
    game = db.q1("SELECT name FROM games WHERE id=?", (p["game_id"],)) if p["game_id"] else {"name": ""}
    program = db.q1("""SELECT p.* FROM programs p JOIN streamer_programs sp ON sp.program_id=p.id
                       WHERE sp.streamer_id=? AND p.active=1 ORDER BY p.payout_per_1k DESC LIMIT 1""",
                    (m["streamer_id"],))
    reqs = compliance.program_requirements(program["id"] if program else None)

    media_path = Path(payload["media"])
    offset = payload.get("media_offset", 0.0)
    start, end = m["start_sec"] - offset, m["end_sec"] - offset
    tcfg = db.get_setting("transcription", {})
    words = transcribe(media_path, tcfg.get("model", "small"), tcfg.get("device", "auto"), max(0, start), end) \
        if tcfg.get("enabled", True) else []
    ctx.progress(0.3)
    transcript = " ".join(w["word"] for w in words)
    pkg = generate_package(transcript, streamer["display_name"], game["name"], reqs["tags"])
    platform_url = {"twitch": "twitch.tv/", "kick": "kick.com/", "youtube": "youtube.com/"}[streamer["platform"]]
    credit = (reqs["credit"] or "{url}").replace("{url}", platform_url + streamer["login"]) \
        .replace("{name}", streamer["display_name"])
    fc = db.get_setting(f"facecam:{streamer['id']}")   # saved facecam box, if detected before
    render_cfg = db.get_setting("render", {})
    cap_cfg = {**db.get_setting("captions", {}), **settings.get("captions", {})}
    out = storage.media_dir() / "clips" / f"{streamer['login']}_{mid}.mp4"
    result = render(RenderSpec(
        source=media_path, start=max(0.0, start), end=end, out=out,
        layout=settings.get("layout", db.get_setting("layout", "auto")),
        facecam=Box(**fc) if fc else None, words=words,
        caption_style=cap_cfg.get("style", "bold_pop"), words_per_chunk=cap_cfg.get("words_per_chunk", 3),
        hook=pkg["hook"], credit=credit, progress_bar=settings.get("progress_bar", True),
        punch_zoom=settings.get("zoom", True),
        music=Path(settings["music"]) if settings.get("music") else None,
        encoder=render_cfg.get("encoder", "auto"), quality=render_cfg.get("quality", "high"),
        fps=render_cfg.get("fps", 30)))
    if result.get("facecam") and not fc:
        db.set_setting(f"facecam:{streamer['id']}", result["facecam"])   # reuse next time
    description = pkg["description"]
    if reqs["disclosure"]:
        description += f"\n{reqs['disclosure']}"
    description += f"\nCredit: {credit}"
    auto = settings.get("auto_approve", False)
    clip_id = db.ex("""INSERT INTO clips(project_id, moment_id, streamer_id, game_id, program_id, file_path, thumb_path,
                         duration_sec, hook_text, title, description, hashtags, layout, caption_style, content_hash,
                         review_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, mid, streamer["id"], p["game_id"], program["id"] if program else None, result["path"],
                     result["thumb"], result["duration"], pkg["hook"], pkg["title"], description,
                     json.dumps(pkg["hashtags"]), result["layout"], cap_cfg.get("style"), result["sha256"],
                     "approved" if auto else "pending"))
    db.ex("UPDATE moments SET status='rendered', transcript=? WHERE id=?", (transcript, mid))
    # Source can be cleaned once no more renders are waiting on it.
    pending = db.q1("""SELECT COUNT(*) n FROM jobs WHERE kind='render_moment' AND status IN ('queued','running')
                       AND id != ? AND json_extract(payload,'$.moment_id') IN
                       (SELECT id FROM moments WHERE source_id=?)""", (ctx.id, m["src_id"]))["n"]
    if not pending:
        db.ex("UPDATE sources SET status='processed' WHERE id=?", (m["src_id"],))
    if media_path.name.startswith(f"src{m['src_id']}_m"):
        media_path.unlink(missing_ok=True)       # VOD section only used for this clip
    emit("clip_ready", clip_id=clip_id, title=pkg["title"], streamer=streamer["display_name"])


@handler("cleanup")
def cleanup(payload: dict, ctx: JobContext) -> None:
    storage.cleanup()


def export_clip(clip_id: int, dest_dir: Path) -> dict:
    """Manual export (MVP): copy the MP4 plus a .txt with title/description/hashtags/credit."""
    c = db.q1("SELECT * FROM clips WHERE id=?", (clip_id,))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dst = dest_dir / Path(c["file_path"]).name
    shutil.copy2(c["file_path"], dst)
    tags = " ".join("#" + t for t in json.loads(c["hashtags"] or "[]"))
    dst.with_suffix(".txt").write_text(f"{c['title']}\n\n{c['description']}\n\n{tags}\n", encoding="utf-8")
    return {"video": str(dst), "caption_file": str(dst.with_suffix(".txt"))}
