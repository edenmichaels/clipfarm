"""My Clips library (v0.8.6): sorting, bulk delete, Recently deleted (7 days), restore and purge.

What a delete does
  - cancels the clip's queued/running render jobs and any post that hasn't gone out yet
    (an upload in progress stops at its next chunk; see posting/upload.py)
  - moves the clip to Recently deleted: hidden everywhere, files still on disk, Restore works for 7 days
What a purge does (after 7 days, or "Empty now")
  - removes the rendered video, the no-music copy, the thumbnail, the editor preview (proxy), the edit
    cache and the edit project (with its version history)
  - keeps the clip row and its posts, views, payouts and submissions, so Earnings, submissions and the
    learning system stay correct
  - never removes the source section (other clips can share it); the storage job removes it once no
    live clip uses it (jobs/storage.py)
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import db

KEEP_DAYS = 7

SORTS: dict[str, str] = {
    "viral": "COALESCE(c.virality, m.score * 100, -1) DESC, c.id DESC",
    "projected": "COALESCE(c.profit_likely, -1) DESC, c.id DESC",
    "date": "c.created_at DESC, c.id DESC",
    "streamer": "lower(COALESCE(s.display_name, '~')), c.id DESC",
    "campaign": "pr.name IS NULL, lower(pr.name), c.id DESC",
    "status": "post_status, c.id DESC",
    "length": "COALESCE(c.duration_sec, 0) DESC, c.id DESC",
}
SORT_LABELS = {"viral": "Viral score", "projected": "Projected A$", "date": "Date made", "streamer": "Streamer",
               "campaign": "Campaign", "status": "Status", "length": "Length"}
DEFAULT_SORT = "viral"
STATUS_NAMES = {0: "not posted", 1: "posted", 2: "submitted"}

# 0 not posted, 1 posted, 2 submitted (to a campaign)
STATUS_SQL = """CASE WHEN EXISTS (SELECT 1 FROM posts p JOIN submissions sb ON sb.post_id=p.id
                                  WHERE p.clip_id=c.id AND sb.status='done') THEN 2
                     WHEN EXISTS (SELECT 1 FROM posts p WHERE p.clip_id=c.id AND p.status='posted') THEN 1
                     ELSE 0 END"""


def sort_key(requested: str | None) -> str:
    """The sort to use: the one asked for (and remembered), else the last one used, else viral score."""
    if requested in SORTS:
        db.set_setting("myclips.sort", requested)
        return requested
    saved = db.get_setting("myclips.sort")
    return saved if saved in SORTS else DEFAULT_SORT


def list_clips(*, game_id=None, streamer_id=None, review_status=None, posted=None, project_id=None, limit=200,
               sort: str | None = None) -> list[dict]:
    """My Clips rows. sort=None keeps the old order (newest first) for other screens; "" means "the remembered
    sort" (viral score by default)."""
    cond, params = ["c.deleted_at IS NULL"], []
    for col, val in (("c.game_id", game_id), ("c.streamer_id", streamer_id),
                     ("c.review_status", review_status), ("c.project_id", project_id)):
        if val is not None:
            cond.append(f"{col}=?")
            params.append(val)
    if posted is not None:
        cond.append(("" if posted else "NOT ") + "EXISTS (SELECT 1 FROM posts p WHERE p.clip_id=c.id AND p.status='posted')")
    key = sort_key(sort) if sort is not None else "date"
    rows = db.q(f"""SELECT c.*, s.display_name streamer, g.name game, pr.name program, m.score,
        (SELECT COALESCE(SUM(pm.views),0) FROM posts p JOIN post_metrics pm ON pm.post_id=p.id
           WHERE p.clip_id=c.id AND pm.taken_at=(SELECT MAX(taken_at) FROM post_metrics WHERE post_id=p.id)) views,
        (SELECT COALESCE(SUM(amount),0) FROM payouts py JOIN posts p ON p.id=py.post_id WHERE p.clip_id=c.id) earnings,
        {STATUS_SQL} post_status
        FROM clips c LEFT JOIN streamers s ON s.id=c.streamer_id LEFT JOIN games g ON g.id=c.game_id
        LEFT JOIN programs pr ON pr.id=c.program_id LEFT JOIN moments m ON m.id=c.moment_id
        WHERE {" AND ".join(cond)} ORDER BY {SORTS[key]} LIMIT ?""", (*params, min(max(int(limit), 1), 20000)))
    for r in rows:
        r["status_name"] = STATUS_NAMES[r["post_status"]]
    return rows


# ---- files -------------------------------------------------------------------------------------------
def _media_dir() -> Path:
    from .jobs import storage
    return storage.media_dir()


def _size(p: Path) -> int:
    try:
        if p.is_dir():
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return p.stat().st_size if p.exists() else 0
    except OSError:
        return 0


def _used_elsewhere(clip_id: int) -> set[str]:
    """Paths that another clip still uses (not purged): never delete those."""
    out: set[str] = set()
    for r in db.q("""SELECT file_path, thumb_path, nomusic_path, source_path FROM clips
                     WHERE id != ? AND purged_at IS NULL""", (clip_id,)):
        out.update(str(Path(v)) for v in r.values() if v)
    return out


def clip_files(c: dict) -> list[Path]:
    """Every file or folder that belongs to this clip alone (what a purge removes)."""
    paths = [c.get(k) for k in ("file_path", "thumb_path", "nomusic_path")]
    proxy = db.get_setting(f"proxy:{c['id']}") or {}
    paths.append(proxy.get("path"))
    cache = _media_dir() / "edit_cache"
    paths.append(str(cache / f"c{c['id']}"))
    if c.get("moment_id") and not db.q1("""SELECT 1 FROM clips WHERE moment_id=? AND id != ? AND purged_at IS NULL""",
                                        (c["moment_id"], c["id"])):
        paths.append(str(cache / f"m{c['moment_id']}"))
    shared = _used_elsewhere(c["id"])
    seen, out = set(), []
    for p in paths:
        if p and str(Path(p)) not in shared and str(Path(p)) not in seen and Path(p).exists():
            seen.add(str(Path(p)))
            out.append(Path(p))
    return out


def clip_bytes(c: dict) -> int:
    return sum(_size(p) for p in clip_files(c))


# ---- jobs and posts that belong to a clip -----------------------------------------------------------------
def _busy(clip_id: int) -> dict:
    jobs = db.q("""SELECT id, kind, status FROM jobs WHERE status IN ('queued','running','paused')
                   AND json_extract(payload,'$.clip_id')=?""", (clip_id,))
    posts = db.q("SELECT id, status FROM posts WHERE clip_id=? AND status IN ('scheduled','uploading','failed')",
                 (clip_id,))
    return {"rendering": any(j["status"] == "running" for j in jobs), "jobs": jobs,
            "uploading": any(p["status"] == "uploading" for p in posts), "posts": posts}


def _cancel(clip_id: int) -> tuple[int, int]:
    """Stop renders and posts for a clip before it's deleted. A running render stops at its next progress
    check; an upload in progress stops before its next chunk (it checks the post's status)."""
    b = _busy(clip_id)
    for j in b["jobs"]:
        db.ex("UPDATE jobs SET status='cancelled', updated_at=datetime('now') WHERE id=?", (j["id"],))
    for p in b["posts"]:
        db.ex("UPDATE posts SET status='cancelled', error='Clip deleted' WHERE id=?", (p["id"],))
    return len(b["jobs"]), len(b["posts"])


def _posted(clip_id: int) -> bool:
    return bool(db.q1("SELECT 1 FROM posts WHERE clip_id=? AND status='posted'", (clip_id,)))


# ---- delete / restore / purge ------------------------------------------------------------------------------
def _live(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return db.q(f"SELECT * FROM clips WHERE id IN ({marks}) AND deleted_at IS NULL", tuple(ids))


def fmt_bytes(n: int) -> str:
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} bytes"


def preview(ids: list[int]) -> dict:
    """What a delete would do, for the confirmation: "Delete 24 clips? This frees 1.8 GB." """
    clips = _live(ids)
    size = sum(clip_bytes(c) for c in clips)
    posted = sum(_posted(c["id"]) for c in clips)
    busy = [c["id"] for c in clips if (b := _busy(c["id"]))["rendering"] or b["uploading"]]
    n = len(clips)
    msg = f"Delete {n} clip{'s' if n != 1 else ''}? This frees {fmt_bytes(size)}."
    notes = []
    if posted:
        notes.append(f"{posted} of these {'was' if posted == 1 else 'were'} posted; "
                     f"{'its' if posted == 1 else 'their'} stats are kept.")
    if busy:
        notes.append(f"{len(busy)} {'is' if len(busy) == 1 else 'are'} rendering or uploading; "
                     "that will be stopped first.")
    notes.append(f"They stay in Recently deleted for {KEEP_DAYS} days in case you change your mind.")
    return {"count": n, "bytes": size, "size": fmt_bytes(size), "posted": posted, "busy": busy,
            "message": msg, "notes": notes}


def delete(ids: list[int]) -> dict:
    clips = _live(ids)
    jobs = posts = 0
    for c in clips:
        j, p = _cancel(c["id"])
        jobs += j
        posts += p
        db.ex(f"""UPDATE clips SET deleted_at=datetime('now'), purge_after=datetime('now','+{KEEP_DAYS} days')
                  WHERE id=?""", (c["id"],))
    posted = sum(_posted(c["id"]) for c in clips)
    db.log("info", "library", f"Deleted {len(clips)} clip(s) (cancelled {jobs} job(s), {posts} post(s))")
    return {"deleted": len(clips), "posted_kept": posted, "cancelled_jobs": jobs, "cancelled_posts": posts,
            "note": (f"{posted} of these {'was' if posted == 1 else 'were'} posted; "
                     f"{'its' if posted == 1 else 'their'} stats are kept.") if posted else ""}


def restore(ids: list[int]) -> dict:
    if not ids:
        return {"restored": 0}
    marks = ",".join("?" * len(ids))
    n = db.q1(f"SELECT COUNT(*) n FROM clips WHERE id IN ({marks}) AND deleted_at IS NOT NULL AND purged_at IS NULL",
              tuple(ids))["n"]
    db.ex(f"""UPDATE clips SET deleted_at=NULL, purge_after=NULL
              WHERE id IN ({marks}) AND deleted_at IS NOT NULL AND purged_at IS NULL""", tuple(ids))
    return {"restored": n}


def trash() -> list[dict]:
    rows = db.q("""SELECT c.id, c.title, c.thumb_path, c.duration_sec, c.deleted_at, c.purge_after, c.virality,
                          s.display_name streamer, pr.name program,
                          CAST(MAX(0, julianday(c.purge_after) - julianday('now')) AS REAL) days_left
                   FROM clips c LEFT JOIN streamers s ON s.id=c.streamer_id LEFT JOIN programs pr ON pr.id=c.program_id
                   WHERE c.deleted_at IS NOT NULL AND c.purged_at IS NULL ORDER BY c.deleted_at DESC""")
    for r in rows:
        full = db.q1("SELECT * FROM clips WHERE id=?", (r["id"],))
        r["bytes"] = clip_bytes(full)
        r["posted"] = _posted(r["id"])
        r["days_left"] = int(-(-r["days_left"] // 1)) if r["days_left"] else 0
    return rows


def purge(ids: list[int] | None = None, *, expired_only: bool = True) -> dict:
    """Remove the files of deleted clips for good. Clips with a render still running are left for next time."""
    cond = ["deleted_at IS NOT NULL", "purged_at IS NULL"]
    params: list = []
    if expired_only:
        cond.append("purge_after <= datetime('now')")
    if ids is not None:
        if not ids:
            return {"purged": 0, "freed": 0, "skipped": 0}
        cond.append(f"id IN ({','.join('?' * len(ids))})")
        params += ids
    freed = purged = skipped = 0
    for c in db.q(f"SELECT * FROM clips WHERE {' AND '.join(cond)}", tuple(params)):
        if db.q1("""SELECT 1 FROM jobs WHERE status='running' AND json_extract(payload,'$.clip_id')=?""", (c["id"],)) or \
                db.q1("SELECT 1 FROM posts WHERE clip_id=? AND status='uploading'", (c["id"],)):
            skipped += 1                     # its file is still being written or read: next time
            continue
        for p in clip_files(c):
            freed += _size(p)
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        db.ex("DELETE FROM edit_projects WHERE clip_id=?", (c["id"],))
        db.ex("DELETE FROM settings WHERE key=?", (f"proxy:{c['id']}",))
        db.ex("""UPDATE clips SET file_path=NULL, thumb_path=NULL, nomusic_path=NULL, purged_at=datetime('now')
                 WHERE id=?""", (c["id"],))
        purged += 1
    if purged:
        db.log("info", "library", f"Removed the files of {purged} deleted clip(s), freed {fmt_bytes(freed)}")
    return {"purged": purged, "freed": freed, "size": fmt_bytes(freed), "skipped": skipped}


def orphan_sources() -> list[Path]:
    """Kept source sections that only deleted-and-purged clips point at (the storage job removes them)."""
    live = {str(Path(r["source_path"])) for r in db.q(
        "SELECT source_path FROM clips WHERE source_path IS NOT NULL AND purged_at IS NULL")}
    out = []
    for r in db.q("SELECT DISTINCT source_path FROM clips WHERE source_path IS NOT NULL AND purged_at IS NOT NULL"):
        p = Path(r["source_path"])
        if str(p) not in live and p.exists():
            out.append(p)
    return out


def remove_orphan_sources() -> int:
    freed = 0
    for p in orphan_sources():
        freed += _size(p)
        p.unlink(missing_ok=True)
        db.ex("UPDATE clips SET source_path=NULL WHERE source_path=? AND purged_at IS NOT NULL", (str(p),))
    return freed
