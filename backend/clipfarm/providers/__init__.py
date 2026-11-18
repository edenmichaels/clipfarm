"""Platform providers. `available_providers()` returns only the ones the user has connected."""
from .base import GameHit, ProviderError, SourceHit, StreamerHit


def available_providers() -> dict:
    out = {}
    for name, factory in (("twitch", "TwitchProvider"), ("youtube", "YouTubeProvider"), ("kick", "KickProvider")):
        try:
            mod = __import__(f"clipfarm.providers.{name}", fromlist=[factory])
            out[name] = getattr(mod, factory)()
        except ProviderError:
            continue
    return out


__all__ = ["GameHit", "ProviderError", "SourceHit", "StreamerHit", "available_providers"]
