"""Turn network/API failures into plain-English messages with a suggested fix.

Used by Setup Health and by every provider so the user sees "Your Twitch Client Secret is wrong"
instead of a stack trace.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

from .redact import redact


@dataclass
class Problem:
    status: str          # red | yellow
    message: str
    fix: str | None = None


def explain_exception(service: str, e: BaseException) -> Problem:
    if isinstance(e, requests.exceptions.SSLError):
        return Problem("red", f"Couldn't make a secure connection to {service}. Check your PC's date and time, "
                              "or whether antivirus is inspecting HTTPS traffic.")
    if isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return Problem("yellow", f"Couldn't reach {service}. You may be offline, or {service} is down. "
                                 "ClipFarm will retry automatically.", "recheck")
    return Problem("red", f"{service} check failed: {redact(e)}")


def explain_response(service: str, r: requests.Response, *, fix_reconnect: str | None = None,
                     fix_keys: str = "open_connections") -> Problem | None:
    """None if the response is OK."""
    if r.status_code < 400:
        return None
    body = (r.text or "")[:600]
    low = body.lower()
    if r.status_code == 429 or "ratelimit" in low or "rate limit" in low:
        retry = r.headers.get("Retry-After")
        return Problem("yellow", f"{service} is rate limiting ClipFarm right now"
                                 + (f" (try again in {retry}s)" if retry else "") + ". It will back off and retry.",
                       "recheck")
    if "quotaexceeded" in low or "dailylimitexceeded" in low:
        return Problem("yellow", f"{service} daily quota is used up. It resets at midnight Pacific time "
                                 "(about 6pm Brisbane time).", None)
    if "accessnotconfigured" in low or "has not been used in project" in low or "is disabled" in low:
        return Problem("red", f"The {service} API isn't switched on in your Google Cloud project. Open the API "
                              "Library, enable 'YouTube Data API v3', then re-check.", "open_youtube_api_library")
    if "invalid_grant" in low or "token has been expired or revoked" in low:
        return Problem("red", f"{service} access was revoked or has expired. Reconnect the account.",
                       fix_reconnect)
    if "keyinvalid" in low or "api key not valid" in low:
        return Problem("red", f"Your {service} API key isn't valid. Copy it again from Google Cloud > "
                              "Credentials and paste it in Settings > Connections.", fix_keys)
    if r.status_code in (400, 401, 403) and ("client" in low or "invalid" in low or "unauthorized" in low):
        return Problem("red", f"{service} rejected your Client ID or Secret. Copy both again from the "
                              f"{service} developer console.", fix_keys)
    if r.status_code >= 500:
        return Problem("yellow", f"{service} is having problems ({r.status_code}). ClipFarm will retry.", "recheck")
    return Problem("red", f"{service} answered {r.status_code}: {redact(body[:200])}", fix_keys)
