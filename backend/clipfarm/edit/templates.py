"""Caption templates: the 4 built-in looks plus your own; a default per account or campaign.

Default resolution for a clip: campaign default -> account default -> your global default -> Bold Pop.
"""
from __future__ import annotations

import json

from .. import db
from ..editing import captions as C


def builtins() -> list[dict]:
    return [{"key": p.key, "name": p.label, "builtin": True, "template": {**p.as_dict(), "base": p.key}}
            for p in C.PRESETS.values()]


def user() -> list[dict]:
    return [{"key": f"user:{r['id']}", "id": r["id"], "name": r["name"], "builtin": False,
             "template": json.loads(r["data"])} for r in db.q("SELECT * FROM caption_templates ORDER BY name")]


def all_templates() -> list[dict]:
    return builtins() + user()


def get(key: str | None) -> dict:
    """A template dict for a key ('bold_pop', 'user:3') -- always valid."""
    if key and key.startswith("user:"):
        r = db.q1("SELECT data FROM caption_templates WHERE id=?", (int(key.split(":")[1]),))
        if r:
            return C.template_from(json.loads(r["data"])).as_dict()
    return C.template_from(key or C.DEFAULT).as_dict()


def save(name: str, template: dict, template_id: int | None = None) -> dict:
    clean = C.template_from(template).as_dict()
    clean["base"] = template.get("base") or clean.get("key")
    if template_id:
        db.ex("UPDATE caption_templates SET name=?, data=? WHERE id=?", (name, json.dumps(clean), template_id))
        tid = template_id
    else:
        tid = db.ex("INSERT INTO caption_templates(name, data) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET data=excluded.data",
                    (name, json.dumps(clean)))
        tid = tid or db.q1("SELECT id FROM caption_templates WHERE name=?", (name,))["id"]
    return {"key": f"user:{tid}", "id": tid, "name": name, "template": clean}


def delete(template_id: int) -> None:
    db.ex("DELETE FROM caption_templates WHERE id=?", (template_id,))
    d = defaults()
    for scope, key in list(d.items()):
        if key == f"user:{template_id}":
            d.pop(scope)
    db.set_setting("caption_defaults", d)


def defaults() -> dict:
    return db.get_setting("caption_defaults", {}) or {}


def set_default(scope: str, key: str | None) -> dict:
    """scope: 'global', 'account:<id>' or 'program:<id>'."""
    d = defaults()
    if key:
        d[scope] = key
    else:
        d.pop(scope, None)
    db.set_setting("caption_defaults", d)
    return d


def default_for(program_id: int | None = None, account_ids: list[int] | None = None) -> str:
    d = defaults()
    if program_id and d.get(f"program:{program_id}"):
        return d[f"program:{program_id}"]
    for a in account_ids or []:
        if d.get(f"account:{a}"):
            return d[f"account:{a}"]
    return d.get("global") or (db.get_setting("captions", {}) or {}).get("style") or C.DEFAULT
