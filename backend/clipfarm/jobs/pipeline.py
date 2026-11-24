"""Job handlers: the MVP pipeline.

project_tick -> fetch_sources (per streamer) -> process_source -> render_moment -> (review) -> export/post
Each handler is idempotent where possible so retries after a crash are safe.
"""
from __future__ import annotations

import json
import logging
import random
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import compliance, db
from ..campaigns import autopilot, matching, rules
from ..discovery.service import rank_game
from ..editing.hooks import generate_options, generate_package
from ..editing.render import RenderSpec, render
from ..music import pick as music_pick
from ..edit import brand as edit_brand, build as edit_build, compile as edit_compile, effects as edit_effects
from ..edit import project as edit_project, templates as edit_templates
from ..highlights import dedupe, detect, learning, rate, run, visual
from ..highlights.chat_logger import ChatLogger, load_chat
from ..highlights.transcribe import transcribe
from ..providers import available_providers
from . import media, storage
from .engine import JobContext, emit, enqueue, handler, on_failure

log = logging.getLogger("clipfarm.pipeline")

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
        left = autopilot.quota_left(p, s["id"])
        if left == 0:
            continue            # auto-pilot: today's clip quota for this streamer is used up
        # Don't pile up fetches when a tick comes round before the last fetch ran (slow network, paused).
        if db.q1("""SELECT 1 FROM jobs WHERE kind='fetch_sources' AND status IN ('queued','running')
                    AND project_id=? AND json_extract(payload,'$.streamer_id')=?""", (pid, s["id"])):
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
    elif s["platform"] == "kick":
        ctx.log(f"{s['display_name']} is on Kick: Kick's official API has no clips or VODs, so ClipFarm "
                "can't clip Kick streams. Use their Twitch/YouTube channel if they have one.", "warn")
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


def _store_viral(mid: int, m) -> None:
    """The viral model's output on the moment: score, event tags (with source times), features, 'Why?' rows."""
    a = int(m.start)
    timeline = [{"tag": t["tag"], "t": a + t["t"], "strength": t["strength"]} for t in (m.tags or [])]
    db.ex("UPDATE moments SET viral_score=?, tags=?, tag_timeline=?, features=?, contributions=? WHERE id=?",
          (m.viral, json.dumps(sorted({t["tag"] for t in timeline})), json.dumps(timeline),
           json.dumps(m.features or {}), json.dumps(m.why or []), mid))


@on_failure("process_source")
def process_source_failed(payload: dict, message: str, final: bool, retry_in: int | None) -> None:
    """A source that keeps failing is retried 3 times (30 s, 2 min, 10 min), then shown as failed with a Retry
    button, and the engine moves on to the next source."""
    sid = payload.get("source_id")
    if final:
        db.ex("UPDATE sources SET status='error', error=?, retry_at=NULL, failed_at=datetime('now') WHERE id=?",
              (message[:500], sid))
        emit("source_failed", source_id=sid, message=message[:200])
    else:
        db.ex("UPDATE sources SET status='new', error=?, retry_at=datetime('now', ?) WHERE id=?",
              (message[:500], f"+{int(retry_in or 0)} seconds", sid))


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
        vis_all = visual.analyse(path) if db.get_setting("highlights", {}).get("visual", True) else {}
        ms, dbg = run.detect_source(path, budget=1, min_len=cfg["min_sec"], max_len=cfg["max_sec"], threshold=0.0,
                                    visual_fn=(lambda a, b: {k: v[int(a):int(b)] for k, v in vis_all.items()})
                                    if vis_all else None, log=ctx.log, game_id=p.get("game_id"), viral_threshold=0)
        dur = float(dbg.get("duration") or 0) or cfg["max_sec"]
        if dur <= cfg["max_sec"]:
            start, end = 0.0, dur
        else:
            start, end = (ms[0].start, ms[0].end) if ms else (0.0, cfg["max_sec"])
        parts = dbg.get("parts") or {}
        mtype = ms[0].type if ms else detect.classify(parts, 0, int(dur)) if parts else "Funny"
        payoff = (ms[0].peak - start) if ms else (end - start) * 0.6
        views = src["view_count"] or 0
        score = round(min(1.0, 0.5 + np.log10(views + 1) / 10), 3)  # community already voted
        fp = dedupe.fingerprint(src["streamer_id"], src["created_at_platform"], 0, src["platform_id"])
        status = "duplicate" if dedupe.is_duplicate(fp) else "queued"
        sigs = {k: v for k, v in (ms[0].signals if ms else {}).items() if not k.startswith("_")}
        mid = db.ex("""INSERT INTO moments(source_id, start_sec, end_sec, score, signals, fingerprint, status, type,
                       reason, detector) VALUES (?,?,?,?,?,?,?,?,?, 'v3')""",
                    (src_id, start, end, score, json.dumps({"clip_views": views, **sigs}),
                     fp, status, mtype, f"{mtype}: community clip with {views:,} views"))
        if ms:
            _store_viral(mid, ms[0])
        if status == "queued" and autopilot.quota_left(p, src["streamer_id"]) != 0:
            enqueue("render_moment", {"project_id": pid, "moment_id": mid, "media": str(path),
                                      "media_offset": 0.0, "streamer_id": src["streamer_id"], "payoff": payoff},
                    pid, priority=3)
        else:
            db.ex("UPDATE sources SET status='processed' WHERE id=?", (src_id,))
        return

    # --- VOD: audio + chat over the whole VOD, visuals/LLM only for the shortlist ------------
    audio_path = media.download_audio(src["url"], work, f"src{src_id}")
    db.ex("UPDATE sources SET status='ready', local_path=? WHERE id=?", (str(audio_path), src_id))
    ctx.progress(0.25)
    vod_start = datetime.fromisoformat(src["created_at_platform"].replace("Z", "+00:00")).timestamp() \
        if src["created_at_platform"] else 0
    chat = []
    login = db.q1("SELECT login FROM streamers WHERE id=?", (src["streamer_id"],))["login"]
    for f in (storage.media_dir() / "chat").glob(f"{login}-*.jsonl"):
        chat += load_chat(f, vod_start)
    clip_offsets = []
    for c in db.q("""SELECT view_count, created_at_platform FROM sources WHERE streamer_id=?
                     AND kind!='vod' AND created_at_platform IS NOT NULL""", (src["streamer_id"],)):
        off = datetime.fromisoformat(c["created_at_platform"].replace("Z", "+00:00")).timestamp() - vod_start
        if off >= 0:
            clip_offsets.append((off, c["view_count"] or 0))
    hcfg = {**db.get_setting("highlights", {}), **settings_of(p).get("highlights", {})}
    weights = learning.weights_for(src["streamer_id"], p["game_id"])
    tcfg = db.get_setting("transcription", {})
    streamer = db.q1("SELECT display_name FROM streamers WHERE id=?", (src["streamer_id"],))
    game = db.q1("SELECT name FROM games WHERE id=?", (p["game_id"],)) if p["game_id"] else {"name": ""}

    def visual_fn(a: float, b: float):
        low = media.download_section(src["url"], work, f"src{src_id}_v{int(a)}", a, b, low_res=True)
        try:
            return visual.analyse(low)
        finally:
            Path(low).unlink(missing_ok=True)

    def words_fn(a: float, b: float):
        if not tcfg.get("enabled", True):
            return []
        sec = media.download_section(src["url"], work, f"src{src_id}_t{int(a)}", a, b, low_res=True)
        try:
            return [{**w, "start": w["start"] + a, "end": w["end"] + a}
                    for w in transcribe(sec, tcfg.get("model", "small"), tcfg.get("device", "auto"))]
        finally:
            Path(sec).unlink(missing_ok=True)

    budget_n = max(1, int(round(cfg["top_n_per_hour"] * max(1.0, _duration(audio_path) / 3600))))
    left = autopilot.quota_left(p, src["streamer_id"])
    if left is not None:
        budget_n = min(budget_n, max(left, 0))
    moments, _dbg = run.detect_source(
        Path(audio_path), chat=chat, clips=clip_offsets, budget=budget_n, min_len=cfg["min_sec"], max_len=cfg["max_sec"],
        focus=hcfg.get("focus", "balanced"), weights=weights,
        visual_fn=visual_fn if hcfg.get("visual", True) else None,
        words_fn=words_fn if hcfg.get("llm", True) else None,
        rate_fn=(lambda tr, det: rate.rate(tr, det, streamer["display_name"], game["name"])) if hcfg.get("llm", True) else None,
        threshold=float(cfg.get("highlight_threshold", 0.42)), log=ctx.log, game_id=p.get("game_id"),
        viral_threshold=int(hcfg.get("viral_threshold", 35))) if budget_n else ([], {})
    ctx.log(f"VOD {src['platform_id']}: {len(moments)} moment(s) picked")
    for i, m in enumerate(moments):
        fp = dedupe.fingerprint(src["streamer_id"], src["created_at_platform"], m.peak, src["platform_id"])
        if dedupe.is_duplicate(fp):
            continue
        words = m.signals.pop("_words", None)
        mid = db.ex("""INSERT INTO moments(source_id, start_sec, end_sec, score, signals, fingerprint, status, type,
                       reason, words, detector) VALUES (?,?,?,?,?,?, 'queued', ?,?,?, 'v3')""",
                    (src_id, m.start, m.end, m.score, json.dumps({k: v for k, v in m.signals.items()
                                                                   if not k.startswith("_")}), fp, m.type, m.reason,
                     json.dumps(words) if words else None))
        _store_viral(mid, m)
        section = media.download_section(src["url"], work, f"src{src_id}_m{mid}", m.start, m.end)
        enqueue("render_moment", {"project_id": pid, "moment_id": mid, "media": str(section),
                                  "media_offset": m.start, "streamer_id": src["streamer_id"],
                                  "payoff": m.peak - m.start}, pid, priority=4)
        ctx.progress(0.3 + 0.7 * (i + 1) / max(1, len(moments)))
    Path(audio_path).unlink(missing_ok=True)   # audio no longer needed
    if not moments:
        db.ex("UPDATE sources SET status='processed' WHERE id=?", (src_id,))


def settings_of(p: dict) -> dict:
    return p.get("settings") or {}


def _duration(path) -> float:
    try:
        from ..editing.ffmpeg_util import probe
        return probe(Path(path))["duration"]
    except Exception:
        return 3600.0


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
    program = _campaign_for(m["streamer_id"], settings)
    req = rules.requirements(program)
    reqs = {"tags": [t.lstrip("#") for t in req["tags"]], "credit": req["credit"], "disclosure": req["disclosure"]}

    media_path = Path(payload["media"])
    offset = payload.get("media_offset", 0.0)
    start, end = m["start_sec"] - offset, m["end_sec"] - offset
    tcfg = db.get_setting("transcription", {})
    if m.get("words"):          # transcribed during detection (source time) -> media time
        words = [{**w, "start": w["start"] - offset, "end": w["end"] - offset} for w in json.loads(m["words"])]
    else:
        words = transcribe(media_path, tcfg.get("model", "small"), tcfg.get("device", "auto"), max(0, start), end) \
            if tcfg.get("enabled", True) else []
    ctx.progress(0.3)
    transcript = " ".join(w["word"] for w in words)
    mtype = m.get("type") or "Funny"
    pkg = generate_package(transcript, streamer["display_name"], game["name"], reqs["tags"])
    platform_url = {"twitch": "twitch.tv/", "kick": "kick.com/", "youtube": "youtube.com/"}[streamer["platform"]]
    credit = (reqs["credit"] or "{url}").replace("{url}", platform_url + streamer["login"]) \
        .replace("{name}", streamer["display_name"])
    fc = db.get_setting(f"facecam:{streamer['id']}")   # saved facecam box, if detected before
    layout, rule_notes, review_flags = rules.choose_layout(settings.get("layout", db.get_setting("layout", "auto")),
                                                           req["onscreen"], bool(fc))
    cap_cfg = {**db.get_setting("captions", {}), **settings.get("captions", {})}
    out = storage.media_dir() / "clips" / f"{streamer['login']}_{mid}.mp4"
    clip_start, clip_end = max(0.0, start), end
    dur = clip_end - clip_start
    payoff = float(payload.get("payoff", dur * 0.6))
    acct_ids = [int(a) for a in (settings.get("per_streamer", {}).get(str(m["streamer_id"]), {}).get("account_ids") or [])]

    # Keep the source section: the clip is an edit project and stays editable.
    keep = storage.media_dir() / "sources" / f"{streamer['login']}_m{mid}{media_path.suffix or '.mp4'}"
    keep.parent.mkdir(parents=True, exist_ok=True)
    if media_path.resolve() != keep.resolve():
        try:
            shutil.move(str(media_path), keep) if media_path.name.startswith(f"src{m['src_id']}_m") else \
                shutil.copy2(media_path, keep)
        except OSError:
            keep = media_path
    # ---- auto-edit -> edit project (EDL) -> export -------------------------------------------------
    edl = edit_build.auto_edit(
        media=keep, start=clip_start, end=clip_end, words=words, streamer=streamer, program=program, req=req,
        settings=settings, moment={"id": mid, "type": mtype, "reason": m.get("reason"), "score": m.get("score"),
                                   "viral": m.get("viral_score"),
                                   "tags": [{**t, "t": round(t["t"] - offset, 2)}
                                            for t in json.loads(m.get("tag_timeline") or "[]")],
                                   "why": json.loads(m.get("contributions") or "[]"),
                                   "setup_start": (lambda v: None if v is None else round(v - offset, 2))(
                                       json.loads(m.get("signals") or "{}").get("setup_start"))},
        hook=pkg["hook"], credit=credit, account_ids=acct_ids, payoff=payoff, category=game["name"])
    if why_off := edl["music"].get("why_off"):
        rule_notes.append(why_off)
    edl = finish_auto_edit(edl, transcript, streamer["display_name"], game["name"], mtype, req["tags"],
                           reqs["disclosure"], credit, acct_ids)
    ctx.progress(0.5)
    tmpl = edit_templates.get(edl["captions"]["template"])
    result = edit_compile.render(edl, out, storage.media_dir() / "edit_cache" / f"m{mid}", "export",
                                 template={**tmpl, **{k: v for k, v in cap_cfg.items() if k in ("uppercase", "emoji") and v is not None}})
    result["layout"] = edl["layout"]["keyframes"][0]["layout"] if edl["layout"]["keyframes"] else "blur_fill"
    result["facecam"] = None
    track = {"id": edl["music"]["track_id"]} if edl["music"].get("enabled") and edl["music"].get("track_id") else None
    plan = {k: edl["music"].get(k) for k in ("offset", "delay", "entry", "mode", "zoom_beats")} if track else None
    if result.get("facecam") and not fc:
        db.set_setting(f"facecam:{streamer['id']}", result["facecam"])   # reuse next time
    description = pkg["description"]
    if reqs["disclosure"]:
        description += f"\n{reqs['disclosure']}"
    description += f"\nCredit: {credit}"
    if req["onscreen"] and layout == rules.FULL_FRAME and result["layout"] != rules.FULL_FRAME:
        review_flags.append("Campaign needs the full frame but a different layout was rendered.")
    caption = rules.caption_text(pkg["title"], pkg["description"], pkg["hashtags"], credit, reqs["disclosure"],
                                 req["tags"])
    missing = rules.check_caption(caption, req)
    if missing:
        review_flags.append("Caption is missing: " + ", ".join(missing))
    auto = settings.get("auto_approve", False) and not review_flags
    clip_id = db.ex("""INSERT INTO clips(project_id, moment_id, streamer_id, game_id, program_id, file_path, thumb_path,
                         duration_sec, hook_text, title, description, hashtags, layout, caption_style, content_hash,
                         review_status, rules_applied, review_flags, caption_text, needs_payout_confirm, words,
                         caption_preset, moment_type, moment_reason, music_track_id, music_plan, base_path, render_spec)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, mid, streamer["id"], p["game_id"], program["id"] if program else None, result["path"],
                     result["thumb"], result["duration"], pkg["hook"], pkg["title"], description,
                     json.dumps(pkg["hashtags"]), result["layout"], cap_cfg.get("style"), result["sha256"],
                     "approved" if auto else "pending", rules.as_json(rule_notes, req), json.dumps(review_flags),
                     caption, int(program is None), json.dumps(words), cap_cfg.get("style", "bold_pop"), mtype,
                     m.get("reason"), track["id"] if track else None, json.dumps(plan) if plan else None,
                     result.get("base"), None))
    db.ex("UPDATE clips SET source_path=? WHERE id=?", (str(keep), clip_id))
    edit_project.create(clip_id, edl, "Auto-edit")
    apply_texts(clip_id, edl, edl["export"].get("preset"))
    edit_project.set_status(clip_id, "ready")
    if track:
        music_pick.record_use(track["id"], clip_id, acct_ids)
    db.ex("UPDATE moments SET status='rendered', transcript=? WHERE id=?", (transcript, mid))
    # Source can be cleaned once no more renders are waiting on it.
    pending = db.q1("""SELECT COUNT(*) n FROM jobs WHERE kind='render_moment' AND status IN ('queued','running')
                       AND id != ? AND json_extract(payload,'$.moment_id') IN
                       (SELECT id FROM moments WHERE source_id=?)""", (ctx.id, m["src_id"]))["n"]
    if not pending:
        db.ex("UPDATE sources SET status='processed' WHERE id=?", (m["src_id"],))
    emit("clip_ready", clip_id=clip_id, title=pkg["title"], streamer=streamer["display_name"])


def spec_json(spec: RenderSpec, result: dict) -> dict:
    """Everything needed to re-render the clip the same way from its base (Review: nudge, fix a word...)."""
    keep = ("start", "end", "caption_style", "words_per_chunk", "caption_nudge", "uppercase", "profanity", "fillers",
            "emoji", "hook", "hook_sec", "credit", "progress_bar", "music_offset",
            "music_entry", "music_delay", "music_track_lufs", "duck_db", "speech", "swells", "quality", "fps")
    d = {k: getattr(spec, k) for k in keep}
    d["music"] = str(spec.music) if spec.music else None
    d["base"] = result.get("base")
    d["layout"] = result.get("layout")
    return d


def _campaign_for(streamer_id: int, settings: dict) -> dict | None:
    """The campaign a clip is made for: the auto-pilot's pick, else the active campaign with the best
    projected earnings, else the best rate."""
    q = settings.get("quotas", {}).get(str(streamer_id))
    if q and q.get("program_id"):
        prog = db.q1("SELECT * FROM programs WHERE id=?", (q["program_id"],))
        if prog:
            return prog
    camps = matching.active_campaigns_for(streamer_id)
    if not camps:
        return None
    def value(c):
        r = db.q1("SELECT MAX(aud_per_pc_hour) v FROM projections WHERE streamer_id=? AND program_id=?",
                  (streamer_id, c["id"]))
        return ((r["v"] if r and r["v"] is not None else -1), c["payout_per_1k"] or 0)
    return max(camps, key=value)


def spec_from_clip(c: dict, changes: dict | None = None) -> RenderSpec:
    """Rebuild a RenderSpec from a clip's stored spec, applying Review changes."""
    d = json.loads(c["render_spec"] or "{}")
    if not d.get("base") or not Path(d["base"]).exists():
        raise PermanentError("This clip's base render is gone (made before v0.4 or cleaned up), so it can't be "
                             "re-rendered. New clips can.")
    ch = changes or {}
    words = ch.get("words") if ch.get("words") is not None else json.loads(c["words"] or "[]")
    music = d.get("music")
    if ch.get("original_audio_only"):
        music = None
    spec = RenderSpec(source=Path(d["base"]), start=d["start"], end=d["end"], out=Path(c["file_path"]),
                      layout=d.get("layout") or "crop", words=words, base=Path(d["base"]),
                      **{k: d[k] for k in ("caption_style", "words_per_chunk", "uppercase", "profanity", "fillers",
                                           "emoji", "hook", "hook_sec", "credit", "progress_bar",
                                           "music_offset", "music_entry", "music_delay", "music_track_lufs",
                                           "duck_db", "quality", "fps") if k in d})
    spec.speech = [tuple(x) for x in d.get("speech") or []]
    spec.swells = [tuple(x) for x in d.get("swells") or []]
    spec.caption_nudge = float(ch.get("caption_nudge", d.get("caption_nudge", 0)) or 0)
    for k in ("caption_style", "uppercase", "emoji", "profanity", "fillers", "hook"):
        if k in ch and ch[k] is not None:
            setattr(spec, k, ch[k])
    spec.music = Path(music) if music else None
    return spec


def finish_auto_edit(edl: dict, transcript: str, streamer: str, game: str, mtype: str | None, tags: list[str],
                     disclosure: str | None, credit: str | None, account_ids: list[int] | None) -> dict:
    """The hands-off part of auto-pilot after the cut: 3-5 hook options and per-platform texts, the account's
    brand template, automatic zooms/effects/SFX/B-roll, then 'Fix my clip'. Each step is best-effort."""
    from ..scoring import fixclip
    try:
        opts = generate_options(transcript, streamer, game, mtype, tags, disclosure, credit)
        edl["hook"]["options"] = opts["hooks"]
        if not edl["hook"].get("text") and opts["hooks"]:
            edl["hook"]["text"] = opts["hooks"][0]
        edl["texts"] = opts["texts"]
    except Exception as e:                                # noqa: BLE001
        db.log("warn", "pipeline", f"Hook options failed: {e}")
    try:
        edl = edit_brand.apply(edl, edit_brand.for_accounts(account_ids))
    except Exception as e:                                # noqa: BLE001
        db.log("warn", "pipeline", f"Brand template failed: {e}")
    fx = db.get_setting("effects", {}) or {}
    if fx.get("auto", True):
        try:
            sf = edl.get("style_fx") or {}          # the clip's Trending Style can switch emoji / SFX off
            edl = edit_effects.auto_effects(edl, emoji=fx.get("emoji", True) and sf.get("emoji", True),
                                            sfx=fx.get("sfx", True) and sf.get("sfx", True),
                                            broll=fx.get("broll", True))
        except Exception as e:                            # noqa: BLE001
            db.log("warn", "pipeline", f"Auto effects failed: {e}")
    try:
        edl, _ = fixclip.fix_my_clip(edl)
    except Exception as e:                                # noqa: BLE001
        db.log("warn", "pipeline", f"Fix my clip failed: {e}")
    return edl


PRESET_TEXT = {"tiktok": "tiktok", "shorts": "youtube_shorts", "reels": "instagram_reels", "x": "x", "srt": "tiktok"}


def apply_texts(clip_id: int, edl: dict, preset: str | None) -> None:
    """Post & Submit gets the title, description and hashtags written for the platform you exported for
    (campaign hashtags, credit and disclosure always included)."""
    plat = PRESET_TEXT.get(preset or edl["export"].get("preset") or "tiktok", "tiktok")
    tx = (edl.get("texts") or {}).get(plat) or (edl.get("texts") or {}).get("tiktok")
    if not tx:
        return
    r = edl["rules"]
    tags = [h.lstrip("#") for h in tx.get("hashtags") or []]
    desc = tx.get("description") or ""
    caption = rules.caption_text(tx.get("title") or "", desc, tags, r.get("credit"), r.get("disclosure"),
                                 [t if t.startswith("#") else "#" + t for t in r.get("tags") or []])
    db.ex("UPDATE clips SET title=COALESCE(NULLIF(?,''), title), description=?, hashtags=?, caption_text=? WHERE id=?",
          (tx.get("title"), desc, json.dumps(tags), caption, clip_id))


def export_edit(clip_id: int, *, preset: str | None = None, music: bool = True, out: Path | None = None,
                quality: str = "export") -> dict:
    """Render a clip's edit project and update the clip (file, thumbnail, hash, status)."""
    p = edit_build.ensure_project(clip_id)
    if not p:
        raise PermanentError("This clip has no editable source any more (it was cleaned up).")
    c = db.q1("SELECT * FROM clips WHERE id=?", (clip_id,))
    edl = p["edl"]
    tmpl = edit_templates.get(edl["captions"]["template"]) if isinstance(edl["captions"]["template"], str) \
        else edl["captions"]["template"]
    target = out or Path(c["file_path"] or storage.media_dir() / "clips" / f"clip_{clip_id}.mp4")
    res = edit_compile.render(edl, target, storage.media_dir() / "edit_cache" / f"c{clip_id}", quality, music=music,
                              template=tmpl, preset=preset)
    if out is None and quality == "export":
        from ..edit import timeline as T
        db.ex("""UPDATE clips SET file_path=?, thumb_path=?, content_hash=?, duration_sec=?, hook_text=COALESCE(?,hook_text),
                 words=?, caption_preset=?, caption_nudge=?, original_audio_only=? WHERE id=?""",
              (res["path"], res["thumb"], res["sha256"], T.duration(edl), edl["hook"].get("text"),
               json.dumps(edl["captions"]["words"]), edl["captions"]["template"] if isinstance(edl["captions"]["template"], str) else "custom",
               edl["captions"].get("nudge", 0), int(not edl["music"].get("enabled")), clip_id))
        apply_texts(clip_id, edl, preset)
        edit_project.set_status(clip_id, "ready")
    return res


@handler("export_edit")
def export_edit_job(payload: dict, ctx: JobContext) -> None:
    head = (edit_project.get(payload["clip_id"]) or {}).get("head")     # the version this render shows
    res = export_edit(payload["clip_id"], preset=payload.get("preset"), music=payload.get("music", True),
                      quality=payload.get("quality", "export"),
                      out=Path(payload["out"]) if payload.get("out") else None)
    if payload.get("quality") == "proxy":
        db.set_setting(f"proxy:{payload['clip_id']}", {"path": res["path"], "head": head})
        emit("preview_ready", clip_id=payload["clip_id"], head=head)       # no desktop notification for previews
        return
    emit("clip_ready", clip_id=payload["clip_id"], title="", streamer="Exported")


@handler("rerender_clip")
def rerender_clip(payload: dict, ctx: JobContext) -> None:
    """Re-render captions/hook/music over the clip's base (seconds, not minutes). Review uses this."""
    c = db.q1("SELECT * FROM clips WHERE id=?", (payload["clip_id"],))
    ch = payload.get("changes") or {}
    if edit_project.get(c["id"]):          # edit-project clips: apply the change to the EDL and export
        def apply(e):
            if ch.get("words") is not None:
                e["captions"]["words"] = ch["words"]
            if ch.get("caption_nudge") is not None:
                e["captions"]["nudge"] = ch["caption_nudge"]
            if ch.get("caption_style"):
                e["captions"]["template"] = ch["caption_style"]
            if ch.get("hook") is not None:
                e["hook"]["text"] = ch["hook"]
            if ch.get("original_audio_only"):
                e["music"]["enabled"] = False
            return e
        edit_project.update(c["id"], apply, "Review edit")
        export_edit(c["id"])
        emit("clip_ready", clip_id=c["id"], title=c["title"], streamer="Re-rendered")
        return
    spec = spec_from_clip(c, ch)
    spec.words = [{**w, "start": float(w["start"]), "end": float(w["end"])} for w in spec.words]
    res = render(spec)
    d = json.loads(c["render_spec"] or "{}")
    d.update({k: v for k, v in ch.items() if k in ("caption_nudge", "caption_style", "uppercase", "emoji",
                                                     "profanity", "fillers", "hook")})
    if ch.get("original_audio_only"):
        d["music"] = None
    db.ex("""UPDATE clips SET words=?, caption_preset=?, caption_nudge=?, hook_text=COALESCE(?, hook_text),
             content_hash=?, thumb_path=?, render_spec=?, original_audio_only=?,
             music_track_id=CASE WHEN ? THEN NULL ELSE music_track_id END WHERE id=?""",
          (json.dumps(spec.words), spec.caption_style, spec.caption_nudge, ch.get("hook"), res["sha256"], res["thumb"],
           json.dumps(d), int(bool(ch.get("original_audio_only"))), int(bool(ch.get("original_audio_only"))), c["id"]))
    emit("clip_ready", clip_id=c["id"], title=c["title"], streamer="Re-rendered")


@handler("export_for_trending")
def export_for_trending(payload: dict, ctx: JobContext) -> None:
    """Same video and captions, stream audio only: add a trending sound in TikTok/Instagram's editor."""
    from ..editing.render import export_without_music
    c = db.q1("SELECT * FROM clips WHERE id=?", (payload["clip_id"],))
    out = Path(c["file_path"]).with_name(Path(c["file_path"]).stem + "_for_trending_sound.mp4")
    if edit_project.get(c["id"]):
        export_edit(c["id"], music=False, out=out)
    else:
        export_without_music(spec_from_clip(c), out)
    db.ex("UPDATE clips SET nomusic_path=? WHERE id=?", (str(out), c["id"]))


@handler("cleanup")
def cleanup(payload: dict, ctx: JobContext) -> None:
    storage.cleanup()


@handler("download_model")
def download_model_job(payload: dict, ctx: JobContext) -> None:
    from ..highlights.transcribe import download_model
    path = download_model(payload.get("size", "small"))
    ctx.log(f"Speech model ready in {path}")
    emit("health", message="Speech model downloaded")


@handler("pull_llm")
def pull_llm(payload: dict, ctx: JobContext) -> None:
    import requests
    r = requests.post(f"{payload['base_url']}/api/pull", json={"model": payload["model"], "stream": False},
                      timeout=3600)
    r.raise_for_status()
    ctx.log(f"Ollama model {payload['model']} downloaded")


@handler("autopilot_tick")
def autopilot_tick(payload: dict, ctx: JobContext) -> None:
    s = autopilot.settings()
    try:
        matching.match_all()
        res = autopilot.tick()
        ctx.log(f"Auto-pilot: {len(res['plan'])} streamer(s) in the plan")
    finally:
        if s["enabled"] and not db.q1("SELECT 1 FROM jobs WHERE kind='autopilot_tick' AND status='queued'"):
            enqueue("autopilot_tick", {}, priority=1, delay_sec=int(s["tick_minutes"]) * 60)


@handler("post_due")
def post_due(payload: dict, ctx: JobContext) -> None:
    """Every 5 minutes: upload posts whose slot has come (YouTube, Instagram, X: posted; TikTok: sent to the
    inbox), then check on TikTok drafts. Quota or draft limits move a post to the next free time; anything only
    the user can fix (sign-in, account setup, no paid X keys) turns the post into a Post Kit."""
    from ..posting import common, platforms as posting
    from ..posting.submissions import PostingBlocked, create_submission_item
    due = db.q("""SELECT p.*, a.platform FROM posts p JOIN accounts a ON a.id=p.account_id
                  WHERE p.status='scheduled' AND p.sent_at IS NULL AND COALESCE(p.stage,'auto') IN ('auto','inbox')
                  AND p.manual=0 AND p.scheduled_at <= datetime('now','localtime') ORDER BY p.scheduled_at""")
    for post in due:
        clip = db.q1("SELECT * FROM clips WHERE id=?", (post["clip_id"],))
        if clip["review_status"] != "approved" or clip["deleted_at"]:
            continue
        acct = db.q1("SELECT * FROM accounts WHERE id=?", (post["account_id"],))
        if posting.mode(acct) == "kit":
            db.ex("UPDATE posts SET stage='kit', manual=1, error=? WHERE id=?",
                  (f"{acct['handle']} can't post automatically right now: use the Post Kit.", post["id"]))
            continue
        db.ex("UPDATE posts SET status='uploading' WHERE id=? AND status='scheduled'", (post["id"],))
        if db.q1("SELECT status FROM posts WHERE id=?", (post["id"],))["status"] != "uploading":
            continue
        try:
            res = posting.post(post["platform"], post["account_id"], clip, post["id"])
            if res["state"] == "posted":
                db.ex("""UPDATE posts SET status='posted', posted_at=datetime('now','localtime'), url=?, external_id=?,
                         stage='auto', error=NULL WHERE id=?""", (res["url"], res["external_id"], post["id"]))
                create_submission_item(post["id"])
                emit("posted", clip_id=clip["id"], url=res["url"])
            else:                                      # TikTok: waiting in the inbox (or direct post processing)
                db.ex("UPDATE posts SET status='scheduled', error=NULL WHERE id=?", (post["id"],))
                emit("posted", clip_id=clip["id"], url="", inbox=True)
        except common.UploadCancelled:
            db.ex("UPDATE posts SET status='cancelled' WHERE id=?", (post["id"],))
        except common.NeedsUser as e:
            db.ex("UPDATE posts SET status='scheduled', stage='kit', manual=1, error=? WHERE id=?", (str(e), post["id"]))
        except PostingBlocked as e:
            db.ex("UPDATE posts SET status='failed', error=? WHERE id=?", (str(e), post["id"]))
        except Exception as e:                           # noqa: BLE001
            nxt = getattr(e, "next_reset", None) or getattr(e, "next_free", None)
            if nxt is not None:                          # quota / draft limit: wait, don't fail
                from datetime import timedelta as _td
                aware = nxt if nxt.tzinfo else nxt.replace(tzinfo=timezone.utc)
                local = aware.astimezone().replace(tzinfo=None) + _td(minutes=random.randint(5, 40))
                db.ex("UPDATE posts SET status='scheduled', scheduled_at=?, error=? WHERE id=?",
                      (local.strftime("%Y-%m-%d %H:%M:%S"), f"Queued: {e}", post["id"]))
                continue
            db.ex("UPDATE posts SET status='scheduled', error=? WHERE id=?", (str(e)[:500], post["id"]))
            raise
    check_tiktok_drafts()
    if not db.q1("SELECT 1 FROM jobs WHERE kind='post_due' AND status='queued'"):
        enqueue("post_due", {}, priority=3, delay_sec=300)


def check_tiktok_drafts() -> int:
    """Drafts sent to the TikTok inbox: once you post one, fill in its link (when TikTok returns the post id) or
    ask you to paste it. Returns how many changed."""
    from ..posting import tiktok
    from ..posting.submissions import record_posted
    changed = 0
    for p in db.q("""SELECT p.*, a.handle FROM posts p JOIN accounts a ON a.id=p.account_id
                     WHERE a.platform='tiktok' AND p.status='scheduled' AND p.stage='inbox' AND p.publish_id IS NOT NULL
                     AND p.sent_at >= datetime('now','-7 days')"""):
        try:
            st = tiktok.fetch_status(p["account_id"], p["publish_id"])
        except Exception as e:                           # noqa: BLE001
            log.warning("TikTok status check failed: %s", e)
            continue
        if st["status"] == "PUBLISH_COMPLETE":
            if st["post_id"]:
                record_posted(p["clip_id"], p["account_id"], tiktok.post_url(p["handle"], st["post_id"]),
                              st["post_id"], manual=False)
            else:
                db.ex("UPDATE posts SET stage='inbox_posted', error=NULL WHERE id=?", (p["id"],))
            changed += 1
        elif st["status"] == "FAILED":
            db.ex("UPDATE posts SET status='failed', error=? WHERE id=?",
                  (f"TikTok: {st.get('fail_reason') or 'upload failed'}", p["id"]))
            changed += 1
    return changed


@handler("metrics")
def metrics(payload: dict, ctx: JobContext) -> None:
    """Pull views for posted YouTube Shorts every 6 hours (others are entered by hand)."""
    from ..posting.youtube import channel_stats
    rows = db.q("""SELECT p.id, p.external_id, p.account_id FROM posts p JOIN accounts a ON a.id=p.account_id
                   WHERE a.platform='youtube' AND p.status='posted' AND p.external_id IS NOT NULL
                   AND p.posted_at >= datetime('now','-30 days')""")
    by_acct: dict[int, list] = {}
    for r in rows:
        by_acct.setdefault(r["account_id"], []).append(r)
    for aid, posts in by_acct.items():
        stats = channel_stats(aid, [p["external_id"] for p in posts])
        for p in posts:
            st = stats.get(p["external_id"])
            if st:
                db.ex("INSERT OR IGNORE INTO post_metrics(post_id, views, likes, comments) VALUES (?,?,?,?)",
                      (p["id"], st["views"], st["likes"], st["comments"]))
    if not db.q1("SELECT 1 FROM jobs WHERE kind='metrics' AND status='queued'"):
        enqueue("metrics", {}, priority=8, delay_sec=6 * 3600)


# ---- viral reference dataset + model (daily) ---------------------------------------------------------------
@handler("viral_refresh")
def viral_refresh(payload: dict, ctx: JobContext) -> None:
    """Collect this week's most-viewed Twitch clips per game (permitted streamers only) and queue feature
    extraction; add your own posts/feedback; then retrain. Runs once a day."""
    from ..highlights import viral_data
    from ..providers import available_providers
    try:
        if (db.get_setting("highlights", {}) or {}).get("viral_dataset", True):
            tw = available_providers().get("twitch")
            games = db.q("""SELECT DISTINCT g.id, g.name, g.twitch_id FROM games g JOIN projects p ON p.game_id=g.id
                            WHERE g.twitch_id IS NOT NULL""")
            permitted = viral_data.permitted_streamers("twitch")
            for g in games if tw and permitted else []:
                try:
                    clips = tw.top_clips_for_game(g["twitch_id"], days=7, n=100)
                except Exception as e:                  # noqa: BLE001
                    ctx.log(f"Top clips for {g['name']}: {e}")
                    continue
                chosen = viral_data.select_clips(clips, permitted)
                have = {r["platform_id"] for r in db.q("SELECT platform_id FROM viral_examples WHERE source_kind='twitch_clip'")}
                for c in chosen:
                    if c["id"] not in have:
                        enqueue("viral_example", {"clip": c, "game_id": g["id"]}, priority=9)
                ctx.log(f"{g['name']}: {len(chosen)} viral clip(s) from permitted streamers")
            yt_channels = [s["platform_user_id"] for s in db.q("SELECT id, platform_user_id FROM streamers WHERE platform='youtube'")
                           if compliance.can_clip(s["id"])[0]]
            if yt_channels:
                viral_data.youtube_short_titles(yt_channels, None)
        viral_data.collect_own()
    finally:
        if not db.q1("SELECT 1 FROM jobs WHERE kind='viral_train' AND status='queued'"):
            enqueue("viral_train", {}, priority=9, delay_sec=3 * 3600)
        if not db.q1("SELECT 1 FROM jobs WHERE kind='viral_refresh' AND status='queued'"):
            enqueue("viral_refresh", {}, priority=9, delay_sec=24 * 3600)


@handler("viral_example")
def viral_example(payload: dict, ctx: JobContext) -> None:
    from ..highlights import viral_data
    c = payload["clip"]
    compliance.assert_can_clip(c["streamer_id"])            # re-check: permission may have changed
    work = storage.media_dir() / "viral_examples"
    viral_data.extract_clip(c, payload["game_id"], work)


@handler("viral_train")
def viral_train(payload: dict, ctx: JobContext) -> None:
    from ..highlights import viral_model
    res = [viral_model.train(None)]
    for g in db.q("SELECT DISTINCT game_id FROM viral_examples WHERE game_id IS NOT NULL"):
        res.append(viral_model.train(g["game_id"]))
    ctx.log("Viral model: " + "; ".join(f"game {r.get('game_id', '?')}: "
                                        + ("trained" if r.get("trained") else f"{r.get('examples', 0)} examples")
                                        for r in res))


@handler("viral_report")
def viral_report(payload: dict, ctx: JobContext) -> None:
    """Detection report on a real VOD (Settings > Highlights > Make detection report):
    dataset size per game, model accuracy, and on one real VOD of a permitted streamer: how many of its
    top community clips each ranking finds (top 10), plus the 20 picked moments with reasons and event tags.
    Community clips are NOT given to the detector here, so finding them is a fair test."""
    from ..highlights import viral_data, viral_model
    from ..providers import available_providers
    tw = available_providers().get("twitch")
    if not tw:
        raise PermanentError("Connect Twitch first (Settings > Connections): the report uses a real Twitch VOD.")
    game_like = payload.get("game") or "Grand Theft Auto VI"
    cands = db.q("""SELECT so.*, s.platform_user_id, s.display_name, s.id sid FROM sources so
                    JOIN streamers s ON s.id=so.streamer_id WHERE so.kind='vod' AND s.platform='twitch'
                    ORDER BY so.created_at_platform DESC""")
    if payload.get("source_id"):
        cands = [c for c in cands if c["id"] == payload["source_id"]]
    vod = next((c for c in cands if compliance.can_clip(c["sid"])[0]), None)
    if not vod:
        raise PermanentError("No VOD from a streamer you're allowed to clip yet. Start a project for "
                             f"{game_like} with a permitted streamer first.")
    work = storage.media_dir() / "reports"
    work.mkdir(parents=True, exist_ok=True)
    clips = tw.clips_in_vod(vod["platform_user_id"], vod["platform_id"], vod["created_at_platform"])
    top = clips[:10]
    ctx.log(f"Report on {vod['display_name']} VOD {vod['platform_id']}: {len(clips)} community clips")
    audio_path = media.download_audio(vod["url"], work, f"report_{vod['id']}")
    ctx.progress(0.2)
    vod_start = datetime.fromisoformat(vod["created_at_platform"].replace("Z", "+00:00")).timestamp()
    chat = []
    login = db.q1("SELECT login FROM streamers WHERE id=?", (vod["sid"],))["login"]
    for f in (storage.media_dir() / "chat").glob(f"{login}-*.jsonl"):
        chat += load_chat(f, vod_start)
    tcfg = db.get_setting("transcription", {})

    def visual_fn(a, b):
        low = media.download_section(vod["url"], work, f"rv{int(a)}", a, b, low_res=True)
        try:
            return visual.analyse(low)
        finally:
            Path(low).unlink(missing_ok=True)

    def words_fn(a, b):
        if not tcfg.get("enabled", True):
            return []
        sec = media.download_section(vod["url"], work, f"rt{int(a)}", a, b, low_res=True)
        try:
            return [{**w, "start": w["start"] + a, "end": w["end"] + a}
                    for w in transcribe(sec, tcfg.get("model", "small"), tcfg.get("device", "auto"))]
        finally:
            Path(sec).unlink(missing_ok=True)
    game = db.q1("SELECT id, name FROM games WHERE name LIKE ?", (f"%{game_like}%",))
    try:
        ms, _ = run.detect_source(Path(audio_path), chat=chat, clips=[], budget=20, min_len=12, max_len=50,
                                  threshold=0.2, visual_fn=visual_fn, words_fn=words_fn,
                                  rate_fn=lambda tr, det: rate.rate(tr, det, vod["display_name"], game_like),
                                  game_id=game["id"] if game else None, viral_threshold=0, log=ctx.log)
    finally:
        Path(audio_path).unlink(missing_ok=True)
    ctx.progress(0.9)

    def found(picks):
        hit = set()
        for c in top:
            a, b = c["vod_offset"], c["vod_offset"] + c["duration"]
            if any(min(b, m.end) - max(a, m.start) >= min(6, c["duration"] / 2) for m in picks):
                hit.add(c["id"])
        return hit
    old_top = sorted(ms, key=lambda m: -m.score)[:10]
    new_top = sorted(ms, key=lambda m: -(m.viral or 0))[:10]
    fo, fn = found(old_top), found(new_top)

    def when(t):
        return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"
    st = viral_data.stats()
    mdl = viral_model.load(game["id"] if game else None)
    lines = [f"# Viral moment detection report: {vod['display_name']}, {game_like}", "",
             f"VOD: {vod['url']} ({vod['created_at_platform'][:10]}). Made {datetime.now().strftime('%Y-%m-%d %H:%M')}.", "",
             "## Dataset", "", "| Game | Viral clips | Not viral | Yours |", "|---|---|---|---|"]
    lines += [f"| {r['game'] or 'any'} | {r['positives']} | {r['negatives']} | {r['yours']} |" for r in st] or ["| (none yet) | | | |"]
    m = mdl.get("metrics") or {}
    lines += ["", f"Model: {'trained ' + str(mdl.get('trained_at')) if mdl.get('trained') else 'starting model (not trained yet)'}"
              + (f"; hold-out AUC {m['auc']:.2f} vs {m['default_auc']:.2f} for the starting model, top-10 precision "
                 f"{m['precision_at_10']:.0%} vs {m['default_precision_at_10']:.0%}" if m.get("auc") is not None else ""), "",
              "## This VOD's top 10 community clips found in the top 10 picks", "",
              "| Ranking | Found |", "|---|---|",
              f"| Before (signal curve, no viral model) | **{len(fo)}/{len(top)}** |",
              f"| After (viral model) | **{len(fn)}/{len(top)}** |", "",
              "## 20 picked moments (after)", "", "| # | Time | Score | Type | Event tags | Reason | Community clip? |",
              "|---|---|---|---|---|---|---|"]
    for i, mm in enumerate(sorted(ms, key=lambda x: -(x.viral or 0))[:20], 1):
        cc = next((c for c in clips if min(c["vod_offset"] + c["duration"], mm.end) - max(c["vod_offset"], mm.start) >= 4), None)
        lines.append(f"| {i} | {when(mm.start)}–{when(mm.end)} | {mm.viral} | {mm.type} | "
                     f"{', '.join(t['tag'] for t in mm.tags) or '-'} | {mm.reason.replace('|', '/')} | "
                     f"{('yes, ' + format(cc['views'], ',') + ' views') if cc else 'no'} |")
    out = work / f"viral_report_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    db.set_setting("viral_report_last", {"path": str(out), "made": datetime.now().isoformat(timespec="minutes"),
                                         "found_before": len(fo), "found_after": len(fn), "top": len(top)})
    emit("report_ready", path=str(out))


@handler("health_check")
def health_check(payload: dict, ctx: JobContext) -> None:
    from .. import health
    res = health.run_all(payload.get("only"))
    bad = [r for r in res if r["status"] == "red"]
    if bad:
        emit("health", message=f"{len(bad)} setup problem(s): " + ", ".join(r["title"] for r in bad))


def export_clip(clip_id: int, dest_dir: Path) -> dict:
    """Manual export (MVP): copy the MP4 plus a .txt with title/description/hashtags/credit."""
    c = db.q1("SELECT * FROM clips WHERE id=?", (clip_id,))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dst = dest_dir / Path(c["file_path"]).name
    shutil.copy2(c["file_path"], dst)
    if c.get("caption_text"):
        text = c["caption_text"] + "\n"
    else:
        tags = " ".join("#" + t for t in json.loads(c["hashtags"] or "[]"))
        text = f"{c['title']}\n\n{c['description']}\n\n{tags}\n"
    dst.with_suffix(".txt").write_text(text, encoding="utf-8")
    return {"video": str(dst), "caption_file": str(dst.with_suffix(".txt"))}
