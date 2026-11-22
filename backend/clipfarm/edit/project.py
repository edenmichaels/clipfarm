"""Edit projects: one JSON edit decision list per clip, versioned, with undo/redo.

Every save is a new version. Undo/redo moves the head; saving after an undo drops the redo branch.
"""
from __future__ import annotations

import copy
import json
import logging

from .. import db

SCHEMA = 1
log = logging.getLogger("clipfarm.edit")
MAX_VERSIONS = 200


def blank(source: dict, words: list[dict], start: float, end: float) -> dict:
    return {
        "v": SCHEMA,
        "source": source,                                   # {path, url, start, end, width, height, fps, has_audio}
        "range": [round(start, 3), round(end, 3)],          # source time
        "cuts": [],                                         # [[a, b]] source time
        "layout": {"mode": "auto", "keyframes": [], "facecam": None, "locked": False},
        "captions": {"template": "bold_pop", "words": words, "nudge": 0.0, "language": None,
                     "translate_to": None, "enabled": True},
        "hook": {"text": None, "options": [], "sec": 2.2},
        "texts": {},                                        # per platform: {title, description, hashtags}
        "zooms": [],                                        # [{t (source), kind: punch|pulse, amount, dur}]
        "overlays": [],                                     # [{kind: emoji|flash|shake|image, t, dur, ...}]
        "broll": [],                                        # [{path, t, dur, licence}]
        "sfx": [],                                          # [{id, path, t, gain_db, licence}]
        "music": {"enabled": True, "track_id": None, "path": None, "offset": 0.0, "entry": 0.0, "delay": 0.0,
                  "volume_db": 0.0, "duck_db": 15.0, "beats": [], "mode": None, "loudness": None},
        "audio": {"speech_gain_db": 0.0, "enhance": False, "denoise": False, "target_lufs": -14.0},
        "moment": {"type": None, "reason": None, "peak": None, "score": None},
        "rules": {"program_id": None, "program": None, "tags": [], "credit": None, "disclosure": None,
                  "onscreen": [], "no_music": False, "platforms": []},
        "brand": None,
        "export": {"preset": "tiktok", "captions": "burned"},
        "progress_bar": True,
    }


def get(clip_id: int) -> dict | None:
    r = db.q1("SELECT * FROM edit_projects WHERE clip_id=?", (clip_id,))
    if not r:
        return None
    return {"id": r["id"], "clip_id": clip_id, "head": r["head"], "edl": json.loads(r["data"]),
            "can_undo": r["head"] > 1, "can_redo": bool(db.q1(
                "SELECT 1 FROM edit_versions WHERE project_id=? AND version>?", (r["id"], r["head"]))),
            "updated_at": r["updated_at"], "status": r["status"]}


def create(clip_id: int, edl: dict, label: str = "Auto-edit") -> dict:
    pid = db.ex("INSERT INTO edit_projects(clip_id, data, head, status) VALUES (?,?,1,'new')", (clip_id, json.dumps(edl)))
    db.ex("INSERT INTO edit_versions(project_id, version, data, label) VALUES (?,?,?,?)", (pid, 1, json.dumps(edl), label))
    _rescore(clip_id, edl)
    return get(clip_id)


def _rescore(clip_id: int, edl: dict) -> None:
    """Virality, projected earnings and rule flags follow every edit. Never blocks saving."""
    try:
        from ..scoring import clipscore
        clipscore.score_clip(clip_id, edl)
    except Exception as e:                              # noqa: BLE001
        log.warning("scoring clip %s failed: %s", clip_id, e)


def save(clip_id: int, edl: dict, label: str = "Edit") -> dict:
    cur = get(clip_id)
    if not cur:
        return create(clip_id, edl, label)
    if json.dumps(edl, sort_keys=True) == json.dumps(cur["edl"], sort_keys=True):
        return cur
    pid, v = cur["id"], cur["head"] + 1
    db.ex("DELETE FROM edit_versions WHERE project_id=? AND version>=?", (pid, v))      # drop the redo branch
    db.ex("INSERT INTO edit_versions(project_id, version, data, label) VALUES (?,?,?,?)", (pid, v, json.dumps(edl), label))
    db.ex("UPDATE edit_projects SET data=?, head=?, status=CASE WHEN status='new' THEN 'edited' ELSE status END, "
          "updated_at=datetime('now') WHERE id=?", (json.dumps(edl), v, pid))
    db.ex("DELETE FROM edit_versions WHERE project_id=? AND version<=?", (pid, v - MAX_VERSIONS))
    _rescore(clip_id, edl)
    return get(clip_id)


def _move(clip_id: int, step: int) -> dict:
    cur = get(clip_id)
    if not cur:
        raise KeyError(clip_id)
    r = db.q1("SELECT version, data FROM edit_versions WHERE project_id=? AND version=?", (cur["id"], cur["head"] + step))
    if not r:
        return cur
    db.ex("UPDATE edit_projects SET data=?, head=?, updated_at=datetime('now') WHERE id=?", (r["data"], r["version"], cur["id"]))
    _rescore(clip_id, json.loads(r["data"]))
    return get(clip_id)


def undo(clip_id: int) -> dict:
    return _move(clip_id, -1)


def redo(clip_id: int) -> dict:
    return _move(clip_id, +1)


def history(clip_id: int) -> list[dict]:
    cur = get(clip_id)
    if not cur:
        return []
    return db.q("SELECT version, label, created_at FROM edit_versions WHERE project_id=? ORDER BY version DESC",
                (cur["id"],))


def set_status(clip_id: int, status: str) -> None:
    db.ex("UPDATE edit_projects SET status=? WHERE clip_id=?", (status, clip_id))


def update(clip_id: int, fn, label: str) -> dict:
    """Apply a pure function edl -> edl and save it as a new version."""
    cur = get(clip_id)
    if not cur:
        raise KeyError(clip_id)
    return save(clip_id, fn(copy.deepcopy(cur["edl"])), label)
