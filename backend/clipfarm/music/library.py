"""The music library: what ClipFarm is allowed to put in your videos, with each track's licence recorded.

Sources:
  bundled  ClipFarm Originals: composed by ClipFarm (compose.py), CC0. Generated on first use into
           %APPDATA%\\ClipFarm\\music\\bundled.
  local    your own music folder (Settings › Music). You are responsible for those licences -- the app
           shows a warning and records "Provided by you" as the licence.
  library  a licensed library you subscribe to, through LibrarySource (none ships yet; see below).

Never added: songs from TikTok/Instagram trending sounds or any commercial release. Trending sounds are
only *names* you add inside the platform's own editor when posting (see trending.py).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from .. import config, db
from ..editing.ffmpeg_util import ffmpeg_bin

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}
LOCAL_LICENCE = "Provided by you (you're responsible for having the rights to use it on TikTok, Reels, Shorts and X)"


def bundled_dir() -> Path:
    return config.app_data_dir() / "music" / "bundled"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def ensure_bundled() -> int:
    """Compose the bundled pack if it isn't on disk yet, and register it. Returns tracks added."""
    from .compose import render_all
    have = db.q1("SELECT COUNT(*) n FROM music_tracks WHERE source='bundled'")["n"]
    d = bundled_dir()
    if have and all(Path(r["path"]).exists() for r in db.q("SELECT path FROM music_tracks WHERE source='bundled'")):
        return 0
    manifest = render_all(d, ffmpeg_bin())
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    from ..editing.render import integrated_lufs
    added = 0
    for m in manifest:
        p = d / m["file"]
        row = db.q1("SELECT id, tags_edited FROM music_tracks WHERE path=?", (str(p),))
        vals = (m["title"], m["artist"], m["licence"], m["licence_url"], m["attribution"], json.dumps(m["moods"]),
                m["bpm"], json.dumps(m["beats"]), m["drop_at"], integrated_lufs(p), m["energy"], 1, m["duration"],
                _sha(p))
        if row:
            db.ex("""UPDATE music_tracks SET title=?, artist=?, licence=?, licence_url=?, attribution=?,
                     moods=CASE WHEN tags_edited=1 THEN moods ELSE ? END, bpm=?, beats=?, drop_at=?, loudness=?,
                     energy=?, instrumental=?, duration=?, sha256=? WHERE id=?""", (*vals, row["id"]))
        else:
            db.ex("""INSERT INTO music_tracks(title, artist, licence, licence_url, attribution, moods, bpm, beats, drop_at,
                     loudness, energy, instrumental, duration, sha256, path, source)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'bundled')""", (*vals, str(p)))
            added += 1
    return added


def scan_local(folder: str | Path) -> dict:
    """Add/refresh tracks from your own folder (auto-tagged). Removed files are disabled, not deleted."""
    from .analyse import analyse
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    seen, added, failed = set(), 0, []
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() not in AUDIO_EXT:
            continue
        seen.add(str(p))
        if db.q1("SELECT 1 FROM music_tracks WHERE path=?", (str(p),)):
            continue
        try:
            a = analyse(p)
        except Exception as e:
            failed.append(f"{p.name}: {e}")
            continue
        db.ex("""INSERT INTO music_tracks(path, title, artist, source, licence, moods, mood_scores, bpm, beats, drop_at,
                 loudness, energy, instrumental, duration, sha256)
                 VALUES (?,?,?, 'local', ?,?,?,?,?,?,?,?,?,?,?)""",
              (str(p), p.stem, None, LOCAL_LICENCE, json.dumps(a["moods"]), json.dumps(a["mood_scores"]), a["bpm"],
               json.dumps(a["beats"]), a["drop_at"], a["loudness"], a["energy"], a["instrumental"], a["duration"], _sha(p)))
        added += 1
    for r in db.q("SELECT id, path FROM music_tracks WHERE source='local'"):
        if r["path"].startswith(str(folder)) and r["path"] not in seen:
            db.ex("UPDATE music_tracks SET enabled=0 WHERE id=?", (r["id"],))
    return {"added": added, "failed": failed, "warning": LOCAL_LICENCE}


def edit(track_id: int, fields: dict) -> dict:
    allowed = {"moods", "enabled", "title", "artist", "licence", "licence_url", "attribution", "instrumental"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if k == "moods" else v)
    if "moods" in fields:
        sets.append("tags_edited=1")
    if sets:
        db.ex(f"UPDATE music_tracks SET {', '.join(sets)} WHERE id=?", (*vals, track_id))
    return get(track_id)


def get(track_id: int) -> dict | None:
    r = db.q1("SELECT * FROM music_tracks WHERE id=?", (track_id,))
    return _row(r) if r else None


def tracks(enabled_only: bool = False) -> list[dict]:
    where = "WHERE enabled=1" if enabled_only else ""
    return [_row(r) for r in db.q(f"SELECT * FROM music_tracks {where} ORDER BY source, title")]


def _row(r: dict) -> dict:
    r = dict(r)
    for k in ("moods", "beats", "mood_scores"):
        r[k] = json.loads(r[k]) if r.get(k) else ([] if k != "mood_scores" else {})
    r["exists"] = Path(r["path"]).exists()
    return r


# ---- licensed libraries ------------------------------------------------------------------------------
class LibrarySource(Protocol):
    """A licensed music library you subscribe to. Implement search() + download() against the library's
    official API, and record its licence for every track. As of Nov 2026 none of the big subscription
    libraries (Epidemic Sound, Artlist, Musicbed) offers an API to individual subscribers, so none
    ships; download their tracks into your local music folder instead."""
    name: str

    def search(self, mood: str, limit: int = 20) -> list[dict]: ...

    def download(self, track_id: str, dest: Path) -> dict: ...
