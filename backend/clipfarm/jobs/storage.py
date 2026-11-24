"""Disk management: delete processed sources, enforce the storage cap (oldest first)."""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import config, db


def media_dir() -> Path:
    d = db.get_setting("media_dir") or str(config.app_data_dir() / "media")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


TEMP_PREFIXES = ("cff_",)


def cleanup_stale_temp(min_age_sec: float = 600) -> int:
    """Remove work folders left in the system temp folder by renders that were killed (crash, power cut, Task
    Manager). Only ClipFarm's own prefixes, and only ones older than min_age_sec. Returns how many were removed."""
    import tempfile
    import time
    root, n = Path(tempfile.gettempdir()), 0
    for d in root.iterdir() if root.exists() else []:
        try:
            if d.is_dir() and d.name.startswith(TEMP_PREFIXES) and time.time() - d.stat().st_mtime > min_age_sec:
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        except OSError:
            continue
    return n


def folder_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def disk_status() -> dict:
    root = media_dir()
    total, used, free = shutil.disk_usage(root)
    cap = int(db.get_setting("storage_cap_gb", 50) * 1024 ** 3)
    mine = folder_size(root)
    return {"media_dir": str(root), "used_by_clipfarm": mine, "cap": cap, "disk_free": free}


def _delete_source(row: dict) -> int:
    freed = 0
    if row["local_path"]:
        for f in Path(row["local_path"]).parent.glob(Path(row["local_path"]).stem.split(".")[0] + "*"):
            if f.is_file() and f.suffix not in (".jsonl",):
                freed += f.stat().st_size
                f.unlink(missing_ok=True)
    db.ex("UPDATE sources SET status='deleted', local_path=NULL WHERE id=?", (row["id"],))
    return freed


def cleanup() -> dict:
    """1) delete processed sources if enabled  2) if still over cap, delete oldest sources, then
    rejected clips. Approved/posted clips are never auto-deleted."""
    from .. import library
    freed = library.purge()["freed"]                 # deleted clips whose 7 days are up
    freed += library.remove_orphan_sources()         # source sections only deleted clips used
    if db.get_setting("delete_sources_after_processing", True):
        for row in db.q("SELECT * FROM sources WHERE status='processed' AND local_path IS NOT NULL"):
            freed += _delete_source(row)
    status = disk_status()
    if status["used_by_clipfarm"] > status["cap"]:
        for row in db.q("SELECT * FROM sources WHERE local_path IS NOT NULL ORDER BY id"):
            freed += _delete_source(row)
            if folder_size(media_dir()) <= status["cap"]:
                break
    if folder_size(media_dir()) > status["cap"]:
        for c in db.q("SELECT * FROM clips WHERE review_status='rejected' AND file_path IS NOT NULL "
                      "AND NOT EXISTS (SELECT 1 FROM posts p WHERE p.clip_id=clips.id AND p.status='posted') ORDER BY id"):
            for p in (c["file_path"], c["thumb_path"]):
                if p and Path(p).exists():
                    freed += Path(p).stat().st_size
                    Path(p).unlink()
            db.ex("UPDATE clips SET file_path=NULL, thumb_path=NULL WHERE id=?", (c["id"],))
    db.log("info", "storage", f"Cleanup freed {freed / 1e6:.1f} MB")
    return {"freed_bytes": freed, **disk_status()}
