"""Common types + a rate-limit-aware HTTP helper shared by all platform providers."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass
class GameHit:
    platform: str
    platform_game_id: str
    name: str
    box_art_url: str | None = None


@dataclass
class StreamerHit:
    platform: str
    platform_user_id: str
    login: str
    display_name: str
    is_live: bool
    viewers: int | None = None
    followers: int | None = None
    title: str | None = None
    avatar_url: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class SourceHit:
    """A clip or VOD we might extract highlights from."""
    kind: str                      # featured_clip | community_clip | vod
    platform_id: str
    url: str
    title: str | None
    view_count: int | None
    duration_sec: float | None
    created_at: str | None
    vod_offset_sec: float | None = None   # for clips: where in the VOD it sits (dedupe)


class ProviderError(RuntimeError):
    pass


class Http:
    """requests.Session wrapper: retries on 429/5xx and honours Retry-After / Ratelimit-Reset."""

    def __init__(self, base_url: str, timeout: float = 20):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.timeout = timeout

    def get(self, path: str, headers: dict, params: Any = None, retries: int = 4) -> dict:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(retries):
            r = self.session.get(url, headers=headers, params=params, timeout=self.timeout)
            if r.status_code == 429 or r.status_code >= 500:
                wait = _retry_wait(r, attempt)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise ProviderError(f"{r.status_code} {url}: {r.text[:300]}")
            return r.json()
        raise ProviderError(f"Gave up after {retries} retries: {url}")


def _retry_wait(r: requests.Response, attempt: int) -> float:
    if "Retry-After" in r.headers:
        try:
            return float(r.headers["Retry-After"])
        except ValueError:
            pass
    reset = r.headers.get("Ratelimit-Reset")  # Twitch: epoch seconds
    if reset and reset.isdigit():
        return max(1.0, int(reset) - time.time())
    return min(30.0, 2 ** attempt)
