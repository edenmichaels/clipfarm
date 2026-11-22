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
from ..campaigns import autopilot, matching, rules
from ..discovery.service import rank_game
from ..editing.hooks import generate_package
from ..editing.layout import Box
from ..editing.render import RenderSpec, render
from ..music import pick as music_pick
from ..highlights import dedupe, detect, learning, rate, run, visual
from ..highlights.chat_logger import ChatLogger, load_chat
from ..highlights.transcribe import transcribe
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
                                    if vis_all else None, log=ctx.log)
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
        mid = db.ex("""INSERT INTO moments(source_id, start_sec, end_sec, score, signals, fingerprint, status, type,
                       reason, detector) VALUES (?,?,?,?,?,?,?,?,?, 'v2')""",
                    (src_id, start, end, score, json.dumps({"clip_views": views, **(ms[0].signals if ms else {})}),
                     fp, status, mtype, f"{mtype}: community clip with {views:,} views"))
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
        threshold=float(cfg.get("highlight_threshold", 0.42)), log=ctx.log) if budget_n else ([], {})
    ctx.log(f"VOD {src['platform_id']}: {len(moments)} moment(s) picked")
    for i, m in enumerate(moments):
        fp = dedupe.fingerprint(src["streamer_id"], src["created_at_platform"], m.peak, src["platform_id"])
        if dedupe.is_duplicate(fp):
            continue
        words = m.signals.pop("_words", None)
        mid = db.ex("""INSERT INTO moments(source_id, start_sec, end_sec, score, signals, fingerprint, status, type,
                       reason, words, detector) VALUES (?,?,?,?,?,?, 'queued', ?,?,?, 'v2')""",
                    (src_id, m.start, m.end, m.score, json.dumps(m.signals), fp, m.type, m.reason,
                     json.dumps(words) if words else None))
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
    render_cfg = db.get_setting("render", {})
    cap_cfg = {**db.get_setting("captions", {}), **settings.get("captions", {})}
    out = storage.media_dir() / "clips" / f"{streamer['login']}_{mid}.mp4"
    clip_start, clip_end = max(0.0, start), end
    dur = clip_end - clip_start
    payoff = float(payload.get("payoff", dur * 0.6))

    # ---- music: licensed library only; never on "original audio only" campaigns/projects ----
    mcfg = db.get_setting("music", {}) or {}
    acct_ids = [int(a) for a in (settings.get("per_streamer", {}).get(str(m["streamer_id"]), {}).get("account_ids") or [])]
    allowed, why_no_music = music_pick.music_allowed(program, settings)
    track = plan = None
    if allowed:
        try:
            track = music_pick.choose(mtype, acct_ids, seed=mid)
        except Exception as e:
            ctx.log(f"Music skipped: {e}")
        if track:
            plan = music_pick.plan(track, mtype, payoff, dur)
    elif why_no_music:
        rule_notes.append(why_no_music)
    speech = [(w["start"] - clip_start, w["end"] - clip_start) for w in words if w["end"] > clip_start]
    swells = [(max(0.0, payoff - 0.3), min(dur, payoff + 2.0))]
    spec = RenderSpec(
        source=media_path, start=clip_start, end=clip_end, out=out, layout=layout,
        facecam=Box(**fc) if fc else None, words=words,
        caption_style=cap_cfg.get("style", "bold_pop"), words_per_chunk=cap_cfg.get("words_per_chunk"),
        uppercase=cap_cfg.get("uppercase"), profanity=cap_cfg.get("profanity", "keep"),
        fillers=cap_cfg.get("fillers", "remove"), emoji=bool(cap_cfg.get("emoji", False)),
        keep_clear=tuple(req["onscreen"]), hook=pkg["hook"], credit=credit,
        progress_bar=settings.get("progress_bar", True), punch_zoom=settings.get("zoom", True),
        music=Path(track["path"]) if track else None, music_offset=plan["offset"] if plan else 0.0,
        music_entry=plan["entry"] if plan else 0.0, music_delay=plan["delay"] if plan else 0.0,
        music_track_lufs=track.get("loudness") if track else None,
        duck_db=float(mcfg.get("duck_db", 15)), speech=speech, swells=swells,
        beats=plan["zoom_beats"] if plan else [],
        encoder=render_cfg.get("encoder", "auto"), quality=render_cfg.get("quality", "high"),
        fps=render_cfg.get("fps", 30))
    result = render(spec)
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
                     result.get("base"), json.dumps(spec_json(spec, result))))
    if track:
        music_pick.record_use(track["id"], clip_id, acct_ids)
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


def spec_json(spec: RenderSpec, result: dict) -> dict:
    """Everything needed to re-render the clip the same way from its base (Review: nudge, fix a word...)."""
    keep = ("start", "end", "caption_style", "words_per_chunk", "caption_nudge", "uppercase", "profanity", "fillers",
            "emoji", "keep_clear", "hook", "hook_sec", "credit", "progress_bar", "punch_zoom", "music_offset",
            "music_entry", "music_delay", "music_track_lufs", "duck_db", "speech", "swells", "beats", "quality", "fps")
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
                                           "emoji", "hook", "hook_sec", "credit", "progress_bar", "punch_zoom",
                                           "music_offset", "music_entry", "music_delay", "music_track_lufs",
                                           "duck_db", "beats", "quality", "fps") if k in d})
    spec.keep_clear = tuple(d.get("keep_clear") or ())
    spec.speech = [tuple(x) for x in d.get("speech") or []]
    spec.swells = [tuple(x) for x in d.get("swells") or []]
    spec.caption_nudge = float(ch.get("caption_nudge", d.get("caption_nudge", 0)) or 0)
    for k in ("caption_style", "uppercase", "emoji", "profanity", "fillers", "hook"):
        if k in ch and ch[k] is not None:
            setattr(spec, k, ch[k])
    spec.music = Path(music) if music else None
    return spec


@handler("rerender_clip")
def rerender_clip(payload: dict, ctx: JobContext) -> None:
    """Re-render captions/hook/music over the clip's base (seconds, not minutes). Review uses this."""
    c = db.q1("SELECT * FROM clips WHERE id=?", (payload["clip_id"],))
    ch = payload.get("changes") or {}
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
    spec = spec_from_clip(c)
    out = Path(c["file_path"]).with_name(Path(c["file_path"]).stem + "_for_trending_sound.mp4")
    export_without_music(spec, out)
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
    """Upload scheduled posts whose time has come (YouTube API). Manual platforms stay in the
    'post manually' list until the user pastes the link."""
    from ..posting import platforms as posting
    from ..posting.submissions import PostingBlocked, create_submission_item
    due = db.q("""SELECT p.*, a.platform FROM posts p JOIN accounts a ON a.id=p.account_id
                  WHERE p.status='scheduled' AND p.manual=0 AND p.scheduled_at <= datetime('now','localtime')""")
    for post in due:
        clip = db.q1("SELECT * FROM clips WHERE id=?", (post["clip_id"],))
        if clip["review_status"] != "approved":
            continue
        db.ex("UPDATE posts SET status='uploading' WHERE id=? AND status='scheduled'", (post["id"],))
        try:
            res = posting.post(post["platform"], post["account_id"], clip)
            db.ex("UPDATE posts SET status='posted', posted_at=datetime('now'), url=?, external_id=? WHERE id=?",
                  (res["url"], res["external_id"], post["id"]))
            create_submission_item(post["id"])
            emit("posted", clip_id=clip["id"], url=res["url"])
        except PostingBlocked as e:
            db.ex("UPDATE posts SET status='failed', error=? WHERE id=?", (str(e), post["id"]))
        except Exception as e:
            db.ex("UPDATE posts SET status='scheduled', error=? WHERE id=?", (str(e)[:500], post["id"]))
            raise
    if not db.q1("SELECT 1 FROM jobs WHERE kind='post_due' AND status='queued'"):
        enqueue("post_due", {}, priority=3, delay_sec=300)


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
