"""Kick official public API (https://docs.kick.com). Client-credentials OAuth.

Register an app at kick.com/settings/developer and paste Client ID/Secret in Settings.
Only the official api.kick.com/public/v1 routes are used — no internal/v2 endpoints.
"""
from __future__ import annotations

import time

import requests

from .. import secrets
from .base import GameHit, Http, ProviderError, StreamerHit

API = "https://api.kick.com/public/v1"
TOKEN_URL = "https://id.kick.com/oauth/token"


class KickProvider:
    platform = "kick"

    def __init__(self):
        self.client_id = secrets.get_secret("kick_client_id")
        self.client_secret = secrets.get_secret("kick_client_secret")
        if not (self.client_id and self.client_secret):
            raise ProviderError("Kick is not connected (add Client ID/Secret in Settings).")
        self.http = Http(API)
        self._token, self._exp = None, 0.0

    def _headers(self) -> dict:
        if not self._token or time.time() > self._exp - 60:
            r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials",
                                              "client_id": self.client_id,
                                              "client_secret": self.client_secret}, timeout=20)
            if r.status_code != 200:
                raise ProviderError(f"Kick auth failed: {r.text[:200]}")
            b = r.json()
            self._token, self._exp = b["access_token"], time.time() + b.get("expires_in", 3600)
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def search_games(self, query: str) -> list[GameHit]:
        data = self.http.get("categories", self._headers(), {"q": query}).get("data", [])
        return [GameHit("kick", str(c["id"]), c["name"], c.get("thumbnail")) for c in data]

    def live_streamers(self, category_id: str, max_results: int = 100) -> list[StreamerHit]:
        data = self.http.get("livestreams", self._headers(),
                             {"category_id": category_id, "sort": "viewer_count",
                              "limit": min(max_results, 100)}).get("data", [])
        return [StreamerHit("kick", str(s["broadcaster_user_id"]), s.get("slug", ""),
                            s.get("slug", ""), True, viewers=s.get("viewer_count"),
                            title=s.get("stream_title"), avatar_url=s.get("profile_picture"))
                for s in data]

    def channel_by_slug(self, slug: str) -> StreamerHit | None:
        data = self.http.get("channels", self._headers(), {"slug": slug.lower()}).get("data", [])
        if not data:
            return None
        c = data[0]
        return StreamerHit("kick", str(c["broadcaster_user_id"]), c.get("slug", slug), c.get("slug", slug), False)

    # Kick's official public API has no clips or VOD endpoints, so Kick channels can be discovered and
    # ranked but not clipped. (ClipFarm doesn't use Kick's private v2 endpoints.)
    def vods(self, channel_id: str, n: int = 5) -> list:
        return []
