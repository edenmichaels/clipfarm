"""Platform providers. `available_providers()` returns only the ones the user has connected."""
from .base import GameHit, ProviderError, SourceHit, StreamerHit
from .kick import KickProvider
from .twitch import TwitchProvider
from .youtube import YouTubeProvider

_FACTORIES = (("twitch", TwitchProvider), ("youtube", YouTubeProvider), ("kick", KickProvider))


def available_providers() -> dict:
    out = {}
    for name, factory in _FACTORIES:
        try:
            out[name] = factory()
        except ProviderError:
            continue  # not connected yet
    return out


__all__ = ["GameHit", "ProviderError", "SourceHit", "StreamerHit", "available_providers"]
