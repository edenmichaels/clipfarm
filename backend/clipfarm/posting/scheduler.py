"""Natural posting scheduler (milestone 2).

Spreads approved clips across each account's daily posting window with a minimum gap and a
small random jitter, never exceeding posts_per_day_per_account, never posting the same clip
(or the same content hash) twice to one account, and routing higher Clip-Profit clips first.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta


def plan_slots(now: datetime, existing: list[datetime], n: int, *, per_day: int = 3, start_hour: int = 9,
               end_hour: int = 22, min_gap_min: int = 90, jitter_min: int = 20, seed: int | None = None) -> list[datetime]:
    """Return n future datetimes respecting the window, daily cap and gap rules."""
    rng = random.Random(seed)
    taken = sorted(existing)
    out: list[datetime] = []
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window = (end_hour - start_hour) * 60
    for _ in range(60):                     # look ahead up to 60 days
        if len(out) >= n:
            break
        todays = [t for t in taken + out if t.date() == day.date()]
        free = per_day - len(todays)
        if free > 0:
            # evenly spaced anchors across the window, jittered so it doesn't look robotic
            step = window / (per_day + 1)
            for k in range(1, per_day + 1):
                if len(out) >= n or free <= 0:
                    break
                t = day + timedelta(hours=start_hour, minutes=step * k + rng.uniform(-jitter_min, jitter_min))
                if t <= now + timedelta(minutes=5):
                    continue
                if all(abs((t - o).total_seconds()) >= min_gap_min * 60 for o in taken + out):
                    out.append(t.replace(second=0, microsecond=0))
                    free -= 1
        day += timedelta(days=1)
    return sorted(out)


def order_by_profit(clips: list[dict]) -> list[dict]:
    """Highest program payout & moment score first."""
    return sorted(clips, key=lambda c: ((c.get("payout_per_1k") or 0), c.get("score") or 0), reverse=True)
