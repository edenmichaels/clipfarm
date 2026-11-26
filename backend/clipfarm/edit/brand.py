"""Brand templates: your look per account (caption style, logo/watermark, intro/outro card, progress bar,
credit placement). One template per account; auto-pilot applies the template of the account a clip is for."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .. import db

FIELDS = {
    "caption_template": None, "logo_path": None, "logo_pos": "tr", "logo_opacity": 0.85, "logo_size": 150,
    "intro_text": "", "outro_text": "", "card_bg": "111111", "card_fg": "FFFFFF", "progress_bar": True,
    "progress_color": "FFE500", "credit_pos": None,
}
POSITIONS = ("tl", "tr", "bl", "br")


def _hex(v, d: str) -> str:
    h = re.sub(r"[^0-9A-Fa-f]", "", str(v or ""))
    return h[:6].upper() if len(h) >= 6 else d


def validate(data: dict) -> dict:
    out = {**FIELDS}
    for k in FIELDS:
        if k in data:
            out[k] = data[k]
    if out["logo_path"]:
        p = Path(str(out["logo_path"]))
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or not p.exists():
            raise ValueError("The logo must be an existing .png, .jpg or .webp file.")
        out["logo_path"] = str(p)
    if out["logo_pos"] not in POSITIONS:
        raise ValueError("logo_pos is tl, tr, bl or br")
    out["logo_opacity"] = max(0.1, min(1.0, float(out["logo_opacity"])))
    out["logo_size"] = int(max(60, min(400, int(out["logo_size"]))))
    out["intro_text"] = str(out["intro_text"] or "")[:80]
    out["outro_text"] = str(out["outro_text"] or "")[:80]
    out["card_bg"], out["card_fg"] = _hex(out["card_bg"], "111111"), _hex(out["card_fg"], "FFFFFF")
    out["progress_color"] = _hex(out["progress_color"], "FFE500")
    out["progress_bar"] = bool(out["progress_bar"])
    if out["credit_pos"] not in (None, "top", "bottom"):
        raise ValueError("credit_pos is top or bottom")
    if out["caption_template"] is not None:
        from . import templates
        if not templates.get(out["caption_template"]):
            raise ValueError("Unknown caption template.")
    return out


def all_templates() -> list[dict]:
    rows = db.q("SELECT * FROM brand_templates ORDER BY name")
    accts = db.q("SELECT id, platform, handle, brand_template_id FROM accounts")
    return [{"id": r["id"], "name": r["name"], **json.loads(r["data"]),
             "accounts": [a["id"] for a in accts if a["brand_template_id"] == r["id"]]} for r in rows]


def get(bid: int) -> dict | None:
    return next((t for t in all_templates() if t["id"] == bid), None)


def save(name: str, data: dict, bid: int | None = None) -> dict:
    name = str(name or "").strip()[:60]
    if not name:
        raise ValueError("Give the template a name.")
    d = validate(data)
    if bid:
        db.ex("UPDATE brand_templates SET name=?, data=? WHERE id=?", (name, json.dumps(d), bid))
    else:
        bid = db.ex("INSERT INTO brand_templates(name, data) VALUES (?,?)", (name, json.dumps(d)))
    return get(bid)


def delete(bid: int) -> None:
    db.ex("UPDATE accounts SET brand_template_id=NULL WHERE brand_template_id=?", (bid,))
    db.ex("DELETE FROM brand_templates WHERE id=?", (bid,))


def assign(account_id: int, bid: int | None) -> None:
    db.ex("UPDATE accounts SET brand_template_id=? WHERE id=?", (bid, account_id))


def for_accounts(account_ids: list[int] | None) -> dict | None:
    for aid in account_ids or []:
        r = db.q1("SELECT brand_template_id FROM accounts WHERE id=?", (aid,))
        if r and r["brand_template_id"]:
            return get(r["brand_template_id"])
    return None


def apply(edl: dict, brand: dict | None) -> dict:
    e = copy.deepcopy(edl)
    if not brand:
        e["brand"] = None
        return e
    e["brand"] = {k: brand.get(k, v) for k, v in FIELDS.items()} | {"id": brand.get("id"), "name": brand.get("name")}
    if brand.get("caption_template"):
        e["captions"]["template"] = brand["caption_template"]
    e["progress_bar"] = bool(brand.get("progress_bar", True))
    return e
