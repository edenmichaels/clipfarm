"""Live / offline / unknown for the streamer pickers (v0.8.8).

  Twitch   Get Streams, up to 100 user ids per call (one call for a normal list)
  Kick     the official public API: /livestreams with up to 50 broadcaster_user_ids per call
  YouTube  no search.list (100 units). The channel's uploads playlist (1 unit) + one videos.list for all of
           them (1 unit per 50): a video with liveStreamingDetails.actualStartTime and no actualEndTime is live.
           Checked at most every 5 minutes, and only while today's live-check budget (default 1,500 units)
           lasts; past that it's "unknown" rather than spending upload quota.
If a check fails: "unknown" with "status unavailable" (a hollow dot), never a guess.
"""
from __future__ import annotations

import time
from datetime import datetime

from .. import db

TTL = {"twitch": 55, "kick": 55, "youtube": 300}
YT_DAILY_UNITS = 1500
_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _unknown(note: str = "status unavailable") -> dict:
    return {"state": "unknown", "note": note}


def _since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _live(viewers, started, game, title) -> dict:
    return {"state": "live", "viewers": int(viewers) if viewers is not None else None, "started_at": started,
            "since": _since(started), "game": game, "title": title}


def twitch(user_ids: list[str], provider=None) -> dict[str, dict]:
    from ..providers.twitch import TwitchProvider
    p = provider or TwitchProvider()
    out = {}
    for i in range(0, len(user_ids), 100):
        chunk = user_ids[i:i + 100]
        live = p.live_status(chunk)                        # ONE Get Streams call per 100 streamers
        for u in chunk:
            s = live.get(u)
            out[u] = _live(s.get("viewer_count"), s.get("started_at"), s.get("game_name"), s.get("title")) if s \
                else {"state": "offline"}
    return out


def kick(user_ids: list[str], provider=None) -> dict[str, dict]:
    from ..providers.kick import KickProvider
    p = provider or KickProvider()
    out = {}
    for i in range(0, len(user_ids), 50):
        chunk = user_ids[i:i + 50]
        params = [("broadcaster_user_id", u) for u in chunk] + [("limit", "100")]
        data = p.http.get("livestreams", p._headers(), params).get("data", [])
        live = {str(d.get("broadcaster_user_id")): d for d in data}
        for u in chunk:
            s = live.get(u)
            out[u] = _live(s.get("viewer_count"), s.get("started_at"), (s.get("category") or {}).get("name"),
                           s.get("stream_title")) if s else {"state": "offline"}
    return out


def _yt_budget(units: int) -> bool:
    day = time.strftime("%Y-%m-%d")
    used = db.get_setting("yt_live_units") or {}
    if used.get("day") != day:
        used = {"day": day, "units": 0}
    cap = int((db.get_setting("live_status") or {}).get("youtube_daily_units", YT_DAILY_UNITS))
    if used["units"] + units > cap:
        return False
    used["units"] += units
    db.set_setting("yt_live_units", used)
    return True


def youtube(channel_ids: list[str], provider=None) -> dict[str, dict]:
    from ..providers.youtube import YouTubeProvider
    p = provider or YouTubeProvider()
    need = len(channel_ids) + max(1, -(-len(channel_ids) * 3 // 50))
    if not _yt_budget(need):
        return {c: _unknown("YouTube live check paused today (saving quota)") for c in channel_ids}
    cand: dict[str, list[str]] = {}
    out: dict[str, dict] = {}
    for c in channel_ids:
        try:
            items = p._get("playlistItems", {"part": "contentDetails", "playlistId": "UU" + c[2:],
                                             "maxResults": 3}).get("items", [])
            cand[c] = [i["contentDetails"]["videoId"] for i in items]
        except Exception:                                  # noqa: BLE001
            out[c] = _unknown()
    ids = [v for vs in cand.values() for v in vs]
    vids = {}
    for i in range(0, len(ids), 50):
        for v in p._get("videos", {"part": "liveStreamingDetails,snippet", "id": ",".join(ids[i:i + 50])}).get("items", []):
            vids[v["id"]] = v
    for c, vs in cand.items():
        out[c] = {"state": "offline"}
        for vid in vs:
            v = vids.get(vid) or {}
            d = v.get("liveStreamingDetails") or {}
            if d.get("actualStartTime") and not d.get("actualEndTime"):
                out[c] = _live(d.get("concurrentViewers"), d["actualStartTime"], None, (v.get("snippet") or {}).get("title"))
                break
    return out


FETCH = {"twitch": twitch, "kick": kick, "youtube": youtube}


def for_streamers(ids: list[int], providers: dict | None = None, now: float | None = None) -> dict[int, dict]:
    """{streamer_id: {state: live|offline|unknown, viewers, since, game, title, note}} with a short cache."""
    now = now or time.time()
    rows = db.q(f"SELECT id, platform, platform_user_id FROM streamers WHERE id IN ({','.join('?' * len(ids))})",
                tuple(ids)) if ids else []
    out: dict[int, dict] = {}
    todo: dict[str, list[dict]] = {}
    for r in rows:
        hit = _cache.get((r["platform"], r["platform_user_id"]))
        if hit and now - hit[0] < TTL.get(r["platform"], 60):
            out[r["id"]] = hit[1]
        else:
            todo.setdefault(r["platform"], []).append(r)
    for plat, rs in todo.items():
        fn = FETCH.get(plat)
        if not fn:
            for r in rs:
                out[r["id"]] = _unknown("no live check for this platform")
            continue
        try:
            res = fn([r["platform_user_id"] for r in rs], (providers or {}).get(plat))
        except Exception:                                  # noqa: BLE001  (not connected, network, quota...)
            res = {}
        for r in rs:
            st = res.get(r["platform_user_id"]) or _unknown()
            if st["state"] != "unknown":
                _cache[(plat, r["platform_user_id"])] = (now, st)
            out[r["id"]] = st
    return out


def clear_cache() -> None:
    _cache.clear()
