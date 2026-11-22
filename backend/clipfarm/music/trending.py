"""Trending sounds, the safe way: names only.

ClipFarm never downloads or embeds a trending song. You keep a list of trending sound names (paste or
import; TikTok and Instagram have no public trending-sounds API for creators as of Nov 2026), and
Review / Post & Submit suggest one that fits the clip's moment type. You add it inside TikTok's or
Instagram's own editor when posting, using the "for trending sound" export (stream audio only, no
ClipFarm music) so the two don't clash.

Paste format, one per line (mood and platform optional):
    Sound name - Artist | funny, hype | tiktok
"""
from __future__ import annotations

import json
import re

from .. import db
from .analyse import MOODS
from .pick import MOOD_FOR_TYPE


def import_text(text: str) -> dict:
    added, updated = 0, 0
    for line in text.splitlines():
        line = line.strip().lstrip("-•*").strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        name_artist = parts[0]
        m = re.match(r"(.+?)\s+[-–—]\s+(.+)", name_artist)
        name, artist = (m.group(1), m.group(2)) if m else (name_artist, None)
        moods = [x.strip().lower() for x in parts[1].split(",")] if len(parts) > 1 else []
        moods = [x for x in moods if x in MOODS]
        platform = parts[2].lower() if len(parts) > 2 and parts[2].lower() in ("tiktok", "instagram", "youtube") else "tiktok"
        url = next((p for p in parts if p.startswith("http")), None)
        if db.q1("SELECT 1 FROM trending_sounds WHERE name=? AND platform=?", (name, platform)):
            db.ex("UPDATE trending_sounds SET artist=COALESCE(?,artist), moods=?, url=COALESCE(?,url), active=1, "
                  "added_at=datetime('now') WHERE name=? AND platform=?", (artist, json.dumps(moods), url, name, platform))
            updated += 1
        else:
            db.ex("INSERT INTO trending_sounds(name, artist, platform, moods, url) VALUES (?,?,?,?,?)",
                  (name, artist, platform, json.dumps(moods), url))
            added += 1
    return {"added": added, "updated": updated}


def listing(active_only: bool = True) -> list[dict]:
    rows = db.q("SELECT * FROM trending_sounds" + (" WHERE active=1" if active_only else "") + " ORDER BY added_at DESC")
    for r in rows:
        r["moods"] = json.loads(r["moods"] or "[]")
    return rows


def suggest(moment_type: str, platform: str = "tiktok", n: int = 3) -> list[dict]:
    wants = MOOD_FOR_TYPE.get(moment_type, ["funny"])
    rows = [r for r in listing() if r["platform"] in (platform, "tiktok")]

    def score(r):
        return (2 if wants[0] in r["moods"] else 1 if any(w in r["moods"] for w in wants) else 0, r["added_at"])
    return sorted(rows, key=score, reverse=True)[:n]
