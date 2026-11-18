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
        res = self._get("search", {"part": "snippet", "channelId": channel_id, "type": "video",
                                   "eventType": "completed", "order": "date", "maxResults": n})
        return [SourceHit("vod", i["id"]["videoId"], f"https://www.youtube.com/watch?v={i['id']['videoId']}",
                          i["snippet"]["title"], None, None, i["snippet"]["publishedAt"])
                for i in res.get("items", [])]
