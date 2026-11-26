"""Twitch Helix provider (official API).

Setup: create an app at https://dev.twitch.tv/console/apps, then paste the Client ID and
Client Secret in Settings > Connections. We use an app access token (client credentials),
which is enough for categories, streams, users, clips and videos.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

from .. import secrets
from .base import GameHit, Http, ProviderError, SourceHit, StreamerHit

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


class TwitchProvider:
    platform = "twitch"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or secrets.get_secret("twitch_client_id")
        self.client_secret = client_secret or secrets.get_secret("twitch_client_secret")
        if not (self.client_id and self.client_secret):
            raise ProviderError("Twitch is not connected (add Client ID/Secret in Settings).")
        self.http = Http(HELIX)
        self._token: str | None = None
        self._token_exp = 0.0

    # ---- auth ---------------------------------------------------------------
    def _headers(self) -> dict:
        if not self._token or time.time() > self._token_exp - 60:
            r = requests.post(TOKEN_URL, data={"client_id": self.client_id,
                                              "client_secret": self.client_secret,
                                              "grant_type": "client_credentials"}, timeout=20)
            if r.status_code != 200:
                raise ProviderError(f"Twitch auth failed: {r.text[:200]}")
            body = r.json()
            self._token, self._token_exp = body["access_token"], time.time() + body["expires_in"]
        return {"Client-Id": self.client_id, "Authorization": f"Bearer {self._token}"}

    # ---- games --------------------------------------------------------------
    def top_games(self, n: int = 40) -> list[GameHit]:
        data = self.http.get("games/top", self._headers(), {"first": min(n, 100)})["data"]
        return [GameHit("twitch", g["id"], g["name"], g["box_art_url"].replace("{width}x{height}", "144x192"))
                for g in data]

    def search_games(self, query: str) -> list[GameHit]:
        data = self.http.get("search/categories", self._headers(), {"query": query, "first": 20})["data"]
        return [GameHit("twitch", g["id"], g["name"], g.get("box_art_url")) for g in data]

    # ---- streamers ----------------------------------------------------------
    def live_streamers(self, game_id: str, max_results: int = 300) -> list[StreamerHit]:
        """Live channels in a category, highest viewers first (Helix returns them sorted)."""
        out, cursor = [], None
        while len(out) < max_results:
            params = {"game_id": game_id, "first": 100}
            if cursor:
                params["after"] = cursor
            body = self.http.get("streams", self._headers(), params)
            for s in body["data"]:
                out.append(StreamerHit("twitch", s["user_id"], s["user_login"], s["user_name"], True,
                                       viewers=s["viewer_count"], title=s["title"],
                                       extra={"started_at": s["started_at"], "language": s["language"]}))
            cursor = body.get("pagination", {}).get("cursor")
            if not cursor or not body["data"]:
                break
        self._attach_profiles(out)
        return out[:max_results]

    def _attach_profiles(self, hits: list[StreamerHit]) -> None:
        for i in range(0, len(hits), 100):
            chunk = hits[i:i + 100]
            params = [("id", h.platform_user_id) for h in chunk]
            users = {u["id"]: u for u in self.http.get("users", self._headers(), params)["data"]}
            for h in chunk:
                u = users.get(h.platform_user_id)
                if u:
                    h.avatar_url = u.get("profile_image_url")

    def users_by_login(self, logins: list[str]) -> list[StreamerHit]:
        """Exact login lookup (used to match campaigns to channels)."""
        out = []
        for i in range(0, len(logins), 100):
            params = [("login", l.lower().lstrip("@")) for l in logins[i:i + 100]]
            for u in self.http.get("users", self._headers(), params)["data"]:
                out.append(StreamerHit("twitch", u["id"], u["login"], u["display_name"], False,
                                       avatar_url=u.get("profile_image_url")))
        return out

    def search_channels(self, query: str, n: int = 10) -> list[StreamerHit]:
        data = self.http.get("search/channels", self._headers(), {"query": query, "first": n})["data"]
        return [StreamerHit("twitch", c["id"], c["broadcaster_login"], c["display_name"], bool(c.get("is_live")),
                            avatar_url=c.get("thumbnail_url")) for c in data]

    def follower_count(self, user_id: str) -> int | None:
        """Total followers. Helix may require a user token for this; returns None if refused."""
        try:
            return self.http.get("channels/followers", self._headers(),
                                 {"broadcaster_id": user_id, "first": 1}).get("total")
        except ProviderError:
            return None

    # ---- sources (clips + VODs) --------------------------------------------
    def clips(self, user_id: str, days: int = 7, featured: bool | None = None, n: int = 50) -> list[SourceHit]:
        started = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"broadcaster_id": user_id, "started_at": started, "first": min(n, 100)}
        if featured is not None:
            params["is_featured"] = str(featured).lower()
        data = self.http.get("clips", self._headers(), params)["data"]
        kind = "featured_clip" if featured else "community_clip"
        return [SourceHit(kind, c["id"], c["url"], c["title"], c["view_count"], c["duration"],
                          c["created_at"], vod_offset_sec=c.get("vod_offset")) for c in data]

    def live_status(self, user_ids: list[str]) -> dict[str, dict]:
        """{user_id: stream} for those of user_ids that are live right now (Get Streams, one light request)."""
        if not user_ids:
            return {}
        params = [("user_id", u) for u in user_ids[:100]] + [("first", "100")]
        data = self.http.get("streams", self._headers(), params).get("data", [])
        return {d["user_id"]: d for d in data}

    def recent_clips(self, user_id: str, since_iso: str) -> list[dict]:
        """Viewer-made clips created since a time (Get Clips). created_at is when the clip was made; a clip covers
        roughly the `duration` seconds before that."""
        data = self.http.get("clips", self._headers(), {"broadcaster_id": user_id, "started_at": since_iso,
                                                         "first": 50}).get("data", [])
        return [{"id": c["id"], "created_at": c["created_at"], "duration": float(c.get("duration") or 30),
                 "views": c.get("view_count", 0), "title": c.get("title"), "url": c.get("url"),
                 "vod_offset": c.get("vod_offset")} for c in data]

    def top_clips_for_game(self, game_id: str, days: int = 7, n: int = 100) -> list[dict]:
        """The most-viewed community clips for a game over the last `days` (Helix returns them by views).
        Official API only: https://dev.twitch.tv/docs/api/reference/#get-clips"""
        started = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out, cursor = [], None
        while len(out) < n:
            params = {"game_id": game_id, "started_at": started, "first": min(100, n - len(out))}
            if cursor:
                params["after"] = cursor
            r = self.http.get("clips", self._headers(), params)
            out += r.get("data", [])
            cursor = (r.get("pagination") or {}).get("cursor")
            if not cursor or not r.get("data"):
                break
        return [{"id": c["id"], "broadcaster_id": c["broadcaster_id"], "broadcaster": c.get("broadcaster_name"),
                 "video_id": c.get("video_id") or None, "vod_offset": c.get("vod_offset"), "views": c["view_count"],
                 "duration": c["duration"], "created_at": c["created_at"], "title": c.get("title"), "url": c["url"]}
                for c in out[:n]]

    def clips_in_vod(self, user_id: str, video_id: str, started_at: str, n: int = 100) -> list[dict]:
        """Community clips made from one VOD (most-viewed first), with their offsets into it."""
        out, cursor = [], None
        while len(out) < n:
            params = {"broadcaster_id": user_id, "started_at": started_at, "first": 100}
            if cursor:
                params["after"] = cursor
            r = self.http.get("clips", self._headers(), params)
            out += [c for c in r.get("data", []) if c.get("video_id") == video_id and c.get("vod_offset") is not None]
            cursor = (r.get("pagination") or {}).get("cursor")
            if not cursor or not r.get("data"):
                break
        return sorted(({"id": c["id"], "vod_offset": float(c["vod_offset"]), "duration": float(c["duration"]),
                        "views": c["view_count"], "title": c.get("title")} for c in out), key=lambda c: -c["views"])[:n]

    def vods(self, user_id: str, n: int = 5) -> list[SourceHit]:
        data = self.http.get("videos", self._headers(), {"user_id": user_id, "type": "archive", "first": n})["data"]
        return [SourceHit("vod", v["id"], v["url"], v["title"], v["view_count"],
                          _parse_twitch_duration(v["duration"]), v["created_at"]) for v in data]


def _parse_twitch_duration(s: str) -> float:
    """'3h12m5s' -> seconds."""
    total, num = 0, ""
    for ch in s:
        if ch.isdigit():
            num += ch
        else:
            total += int(num or 0) * {"h": 3600, "m": 60, "s": 1}[ch]
            num = ""
    return float(total)
