"""Make sure the same real-world moment is never clipped twice.

A moment is identified by (streamer, wall-clock time bucket). Wall-clock = source broadcast start
+ offset, so a community clip and the VOD region it came from collide on the same fingerprint.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .. import db

BUCKET_SEC = 30


def fingerprint(streamer_id: int, broadcast_start_iso: str | None, offset_sec: float, source_key: str) -> str:
    if broadcast_start_iso:
        t = datetime.fromisoformat(broadcast_start_iso.replace("Z", "+00:00")) + timedelta(seconds=offset_sec)
        bucket = int(t.timestamp() // BUCKET_SEC)
        return f"{streamer_id}:{bucket}"
    return f"{streamer_id}:{source_key}:{int(offset_sec // BUCKET_SEC)}"


def is_duplicate(fp: str) -> bool:
    """True if this fingerprint or an adjacent bucket was already kept."""
    sid, _, rest = fp.partition(":")
    if rest.isdigit():
        b = int(rest)
        keys = [f"{sid}:{b + d}" for d in (-1, 0, 1)]
    else:
        keys = [fp]
    marks = ",".join("?" * len(keys))
    return db.q1(f"""SELECT 1 FROM moments WHERE fingerprint IN ({marks})
                     AND status IN ('candidate','queued','rendered')""", keys) is not None
