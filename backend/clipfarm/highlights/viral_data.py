"""The viral reference dataset: real evidence of what goes viral, built automatically and legally.

Positives
- Twitch's most-viewed community clips per game over the last 7 days (official Helix "Get Clips" API, by game,
  sorted by views). Viewers chose to clip these moments and watch them most.
  ONLY for streamers ClipFarm is allowed to clip (permission gate). For each: the matching VOD time range is
  fetched (official VOD, low-res), features are extracted, and the view count is stored.
- Your own posts that beat their predicted views, your 👍 and "keep/export" in the Editor.
- Style references you add yourself (tags of what happened), as trend evidence only.
Negatives
- Random windows from the same VODs that nobody clipped, plus mundane windows (menus, loading, calm driving,
  cutscenes, sad/serious talk), your 👎 and deleted clips, and posts that flopped.

Never downloads or scrapes TikTok or Instagram. YouTube is used only through the official Data API, for Shorts
titles of channels you're allowed to clip (trend tags only: nothing is downloaded).
Refreshed daily (job "viral_refresh"), then the model retrains ("viral_train").
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import compliance, db

ALLOWED_HOSTS = ("api.twitch.tv", "id.twitch.tv", "www.twitch.tv", "www.googleapis.com")   # nothing else
PER_GAME_PER_DAY = 25           # positives per game per refresh (each needs a short VOD download)
CONTEXT = 30.0                  # seconds of VOD around each window, for "normal for this channel" baselines


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def permitted_streamers(platform: str = "twitch") -> dict[str, int]:
    """platform user id -> streamer id, only for streamers we may clip."""
    out = {}
    for s in db.q("SELECT id, platform_user_id FROM streamers WHERE platform=?", (platform,)):
        if compliance.can_clip(s["id"])[0]:
            out[str(s["platform_user_id"])] = s["id"]
    return out


def select_clips(clips: list[dict], permitted: dict[str, int], limit: int = PER_GAME_PER_DAY) -> list[dict]:
    """Keep only permitted streamers' clips that still point into a VOD, most-viewed first."""
    keep = [c for c in clips if str(c["broadcaster_id"]) in permitted and c.get("video_id") and c.get("vod_offset")
            is not None]
    keep.sort(key=lambda c: -int(c.get("views") or 0))
    return [{**c, "streamer_id": permitted[str(c["broadcaster_id"])]} for c in keep[:limit]]


def negative_offsets(clip_offsets: list[float], vod_len: float, n: int, seed: int, length: float = 30.0,
                     gap: float = 120.0) -> list[float]:
    """Random windows of the same VOD at least `gap` s from any clipped moment."""
    rng = random.Random(seed)
    out: list[float] = []
    tries = 0
    while len(out) < n and tries < 200 and vod_len > length + 60:
        tries += 1
        t = rng.uniform(30, max(31, vod_len - length - 30))
        if all(abs(t - c) > gap for c in clip_offsets) and all(abs(t - o) > length for o in out):
            out.append(round(t, 1))
    return out


def add_example(*, game_id, streamer_id, kind, platform_id, label, features, tags=None, vod_id=None, vod_offset=None,
                duration=None, views=None, weight=1.0, title=None, day=None) -> bool:
    try:
        db.ex("""INSERT INTO viral_examples(game_id, streamer_id, source_kind, platform_id, vod_id, vod_offset, duration,
                 views, label, weight, features, tags, title, day) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              # -1 = "no VOD offset": NULLs never collide in a UNIQUE index, which would store duplicates
              (game_id, streamer_id, kind, platform_id, vod_id, -1.0 if vod_offset is None else float(vod_offset),
               duration, views, float(label), float(weight),
               json.dumps(features) if features is not None else None, json.dumps(tags or []), title, day or _today()))
        return True
    except Exception:            # noqa: BLE001 (UNIQUE: already have it)
        return False


# ---- feature extraction for one VOD window -------------------------------------------------------------------
def window_features(media: Path, a: float, b: float, *, chat: list[tuple[float, str]] | None = None,
                    words: list[dict] | None = None) -> tuple[dict, list[str], dict]:
    """Features of [a, b) seconds inside `media` (a short section with context around it)."""
    from . import audio_events, detect, events, features, signals
    loud = signals.audio_rms(media)
    n = len(loud)
    ev = audio_events.analyse(media)
    sig = detect.Signals(n, events=ev, loudness=loud, chat=signals.chat_reactions(chat, n) if chat else None)
    comp, parts = detect.components(sig)
    extra = features.per_second(parts, comp, n, words)
    tags = events.from_signals(n, parts, words, chat)
    f = features.window(parts, comp, extra, tags, int(a), int(np.ceil(b)))
    tag_names = sorted({t["tag"] for t in events.summarise(tags, int(a), int(np.ceil(b)))})
    return f, tag_names, {"duration": n}


def extract_clip(c: dict, game_id: int, work: Path, *, negatives: int = 1, vod_len: float | None = None,
                 transcribe_fn=None) -> int:
    """Download the clip's VOD range (low-res) + context, store the positive and `negatives` random windows."""
    from ..jobs import media
    url = f"https://www.twitch.tv/videos/{c['video_id']}"
    off, dur = float(c["vod_offset"]), float(c["duration"])
    a0 = max(0.0, off - CONTEXT)
    sec = media.download_section(url, work, f"vx_{c['id']}", a0, off + dur + 10, low_res=True)
    added = 0
    try:
        words = transcribe_fn(sec) if transcribe_fn else None
        f, tags, _ = window_features(sec, off - a0, off - a0 + dur, words=words)
        w = 1.0 + np.log10(max(10, int(c.get("views") or 0))) / 4      # more views, more weight
        added += add_example(game_id=game_id, streamer_id=c["streamer_id"], kind="twitch_clip", platform_id=c["id"],
                             label=1.0, weight=round(float(w), 3), features=f, tags=tags, vod_id=c["video_id"],
                             vod_offset=off, duration=dur, views=c.get("views"), title=c.get("title"))
    finally:
        Path(sec).unlink(missing_ok=True)
    for t in negative_offsets([off], vod_len or off + 3600, negatives, seed=hash(c["id"]) & 0xFFFF):
        try:
            sec = media.download_section(url, work, f"vn_{c['id']}_{int(t)}", max(0, t - CONTEXT), t + 30 + 10,
                                         low_res=True)
        except Exception:        # noqa: BLE001
            continue
        try:
            a = min(CONTEXT, t)
            f, tags, _ = window_features(sec, a, a + 30)
            mundane = max(f["menu"], f["music_only"], f["driving"], f["sad"], f["calm_talk"]) >= 0.4
            added += add_example(game_id=game_id, streamer_id=c["streamer_id"], kind="mundane" if mundane else "negative",
                                 platform_id=c["video_id"], label=0.0, features=f, tags=tags, vod_id=c["video_id"],
                                 vod_offset=t, duration=30)
        finally:
            Path(sec).unlink(missing_ok=True)
    return added


# ---- your own data -------------------------------------------------------------------------------------------
def collect_own() -> int:
    """Your posts (real views vs predicted), 👍/👎, keep/export/delete in the Editor -> examples."""
    added = 0
    rows = db.q("""SELECT c.id, c.game_id, c.streamer_id, c.expected_views, m.features, m.tags,
                   (SELECT MAX(pm.views) FROM posts p JOIN post_metrics pm ON pm.post_id=p.id WHERE p.clip_id=c.id) views,
                   (SELECT rating FROM clip_feedback f WHERE f.clip_id=c.id) rating,
                   (SELECT GROUP_CONCAT(kind) FROM clip_signals s WHERE s.clip_id=c.id) sigs
                   FROM clips c JOIN moments m ON m.id=c.moment_id WHERE m.features IS NOT NULL""")
    for r in rows:
        f = json.loads(r["features"])
        tags = json.loads(r["tags"] or "[]")
        sigs = set((r["sigs"] or "").split(","))
        if r["views"] and r["expected_views"]:
            added += add_example(game_id=r["game_id"], streamer_id=r["streamer_id"], kind="my_post",
                                 platform_id=f"clip{r['id']}", label=1.0 if r["views"] >= r["expected_views"] else 0.0,
                                 weight=1.5, features=f, tags=tags, views=r["views"])
        lab = None
        if r["rating"]:
            lab = 1.0 if r["rating"] > 0 else 0.0
        elif "delete" in sigs:
            lab = 0.0
        elif sigs & {"keep", "export"}:
            lab = 1.0
        if lab is not None:
            added += add_example(game_id=r["game_id"], streamer_id=r["streamer_id"], kind="feedback",
                                 platform_id=f"clip{r['id']}", label=lab, weight=1.2 if r["rating"] else 0.8,
                                 features=f, tags=tags)
    return added


_TITLE_TAG = re.compile(r"[a-z]+")


def youtube_short_titles(channel_ids: list[str], game_id: int | None, days: int = 7) -> int:
    """Top Shorts titles (official YouTube Data API) of channels you may clip -> trend tags. Nothing downloaded."""
    from ..providers import available_providers
    from .events import KEYWORDS
    yt = available_providers().get("youtube")
    if not yt:
        return 0
    from datetime import timedelta
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kw = {w: t for t, ws in KEYWORDS.items() for w in ws}
    added = 0
    for ch in channel_ids[:5]:                   # search.list costs 100 quota units each
        try:
            r = yt._get("search", {"part": "snippet", "channelId": ch, "type": "video", "videoDuration": "short",
                                   "order": "viewCount", "publishedAfter": after, "maxResults": 10})
        except Exception:        # noqa: BLE001
            continue
        for it in r.get("items", []):
            title = it["snippet"]["title"]
            tags = sorted({kw[w] for w in _TITLE_TAG.findall(title.lower()) if w in kw})
            if tags:
                added += add_example(game_id=game_id, streamer_id=None, kind="youtube_short",
                                     platform_id=it["id"]["videoId"], label=1.0, weight=0.5, features=None, tags=tags,
                                     title=title)
    return added


def stats() -> list[dict]:
    return db.q("""SELECT e.game_id, g.name game, COUNT(*) total, SUM(e.label > 0.5) positives,
                   SUM(e.label <= 0.5) negatives, SUM(e.source_kind='twitch_clip') twitch_clips,
                   SUM(e.source_kind IN ('my_post','feedback')) yours, MAX(e.created_at) updated
                   FROM viral_examples e LEFT JOIN games g ON g.id=e.game_id GROUP BY e.game_id ORDER BY total DESC""")
