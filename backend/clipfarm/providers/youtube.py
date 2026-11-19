"""YouTube Data API v3 provider (discovery only; uploads live in posting/youtube.py).

Quota note: search.list costs 100 units of the default 10,000/day, so discovery is cached
and refreshed at most every `ranking_refresh_minutes`.
"""
from __future__ import annotations

from .. import secrets
from .base import Http, ProviderError, SourceHit, StreamerHit

API = "https://www.googleapis.com/youtube/v3"


class YouTubeProvider:
    platform = "youtube"

    def __init__(self, api_key: str | None = None):
        self.key = api_key or secrets.get_secret("youtube_api_key")
        if not self.key:
            raise ProviderError("YouTube is not connected (add an API key in Settings).")
        self.http = Http(API)

    def _get(self, path: str, params: dict) -> dict:
        return self.http.get(path, {}, {**params, "key": self.key})

    def live_streamers(self, game_query: str, max_results: int = 50) -> list[StreamerHit]:
        search = self._get("search", {"part": "snippet", "q": game_query, "type": "video",
                                      "eventType": "live", "videoCategoryId": "20",
                                      "maxResults": min(max_results, 50), "order": "viewCount"})
        items = search.get("items", [])
        if not items:
            return []
        video_ids = ",".join(i["id"]["videoId"] for i in items)
        videos = {v["id"]: v for v in self._get("videos", {"part": "liveStreamingDetails,snippet",
                                                          "id": video_ids})["items"]}
        channel_ids = ",".join({i["snippet"]["channelId"] for i in items})
        channels = {c["id"]: c for c in self._get("channels", {"part": "statistics,snippet",
                                                              "id": channel_ids})["items"]}
        out = []
        for it in items:
            vid, ch_id = it["id"]["videoId"], it["snippet"]["channelId"]
            ch = channels.get(ch_id, {})
            stats = ch.get("statistics", {})
            live = videos.get(vid, {}).get("liveStreamingDetails", {})
            out.append(StreamerHit(
                "youtube", ch_id, ch.get("snippet", {}).get("customUrl", ch_id),
                it["snippet"]["channelTitle"], True,
                viewers=int(live["concurrentViewers"]) if "concurrentViewers" in live else None,
                followers=None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0)),
                title=it["snippet"]["title"],
                avatar_url=ch.get("snippet", {}).get("thumbnails", {}).get("default", {}).get("url"),
                extra={"video_id": vid}))
        return out

    def vods(self, channel_id: str, n: int = 5) -> list[SourceHit]:
        """Finished livestreams, newest first. Uses the uploads playlist (about 3 quota units) instead of
        search.list (100 units), which ran out the daily quota within an hour when called every tick."""
        ch = self._get("channels", {"part": "contentDetails", "id": channel_id}).get("items", [])
        if not ch:
            return []
        uploads = ch[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        items = self._get("playlistItems", {"part": "contentDetails,snippet", "playlistId": uploads,
                                            "maxResults": 15}).get("items", [])
        if not items:
            return []
        ids = [i["contentDetails"]["videoId"] for i in items]
        vids = self._get("videos", {"part": "liveStreamingDetails,contentDetails,statistics,snippet",
                                    "id": ",".join(ids)}).get("items", [])
        out = []
        for v in vids:
            live = v.get("liveStreamingDetails") or {}
            if not live.get("actualEndTime"):
                continue        # only finished streams are VODs we can analyse
            out.append(SourceHit("vod", v["id"], f"https://www.youtube.com/watch?v={v['id']}",
                                 v["snippet"]["title"], int(v.get("statistics", {}).get("viewCount", 0) or 0),
                                 _iso_duration(v.get("contentDetails", {}).get("duration", "")),
                                 live.get("actualStartTime") or v["snippet"]["publishedAt"]))
        return out[:n]

    def channel_by_handle(self, handle: str) -> StreamerHit | None:
        """@handle -> channel (1 quota unit)."""
        items = self._get("channels", {"part": "snippet,statistics", "forHandle": handle.lstrip("@")}).get("items", [])
        if not items:
            return None
        c = items[0]
        stats = c.get("statistics", {})
        return StreamerHit("youtube", c["id"], c["snippet"].get("customUrl", handle), c["snippet"]["title"], False,
                           followers=None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0)),
                           avatar_url=c["snippet"].get("thumbnails", {}).get("default", {}).get("url"))


def _iso_duration(s: str) -> float | None:
    """'PT3H12M5S' -> seconds."""
    import re
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60 + se)
