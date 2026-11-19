"""Campaign field definitions and the "needs confirming" rule.

A field needs confirming unless it was filled automatically (read from the campaign page or an
import) or by the user. Fields that only hold a default still count in projections, with a warning.
"""
from __future__ import annotations

import json

# Every field the Programs/Campaigns screen tracks per campaign, with the default used in
# projections when it is unknown.
FIELDS: dict[str, object] = {
    "campaign_type": "pay_per_view",
    "payout_per_1k": None,
    "min_views": None,            # derived from min_payout / rate when not given
    "min_payout": None,
    "max_payout": None,
    "pool_size": None,
    "view_threshold": None,
    "budget_total": None,
    "budget_remaining": None,
    "ends_at": None,
    "required_tags": [],
    "required_onscreen": [],
    "required_credit": "{url}",
    "disclosure": None,
    "allowed_platforms": ["tiktok", "youtube_shorts", "instagram_reels"],
    "pays_au": 1,
    "fee": 0.10,
    "approval_rate": 0.60,
}

# Fields that only matter for one campaign type.
POOL_ONLY = {"pool_size", "view_threshold"}
PPV_ONLY = {"payout_per_1k", "min_payout", "max_payout", "budget_total", "budget_remaining"}
TRUSTED_SOURCES = {"page", "user", "import", "api"}


def needs_confirming(row: dict) -> list[str]:
    """Fields of this campaign that weren't filled automatically or by the user."""
    sources = _load(row.get("field_sources"), {})
    ctype = row.get("campaign_type") or "pay_per_view"
    out = []
    for f in FIELDS:
        if ctype == "prize_pool" and f in PPV_ONLY:
            continue
        if ctype == "pay_per_view" and f in POOL_ONLY:
            continue
        if f == "min_views" and (sources.get("min_payout") in TRUSTED_SOURCES):
            continue      # derived from the per-clip minimum payout
        if sources.get(f) not in TRUSTED_SOURCES:
            out.append(f)
    return out


def effective(row: dict, field: str):
    """Value used by projections: the stored value, else the default."""
    v = row.get(field)
    if field in ("required_tags", "required_onscreen", "allowed_platforms"):
        v = _load(v, None)
    if v is None or v == []:
        if field == "min_views" and row.get("min_payout") and row.get("payout_per_1k"):
            return int(round(float(row["min_payout"]) / float(row["payout_per_1k"]) * 1000))
        return FIELDS.get(field)
    return v


def refresh_flags(conn, program_id: int) -> list[str]:
    row = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    cols = [d[0] for d in conn.execute("SELECT * FROM programs LIMIT 0").description]
    flags = needs_confirming(dict(zip(cols, row)))
    conn.execute("UPDATE programs SET needs_confirming=? WHERE id=?", (json.dumps(flags), program_id))
    return flags


def _load(v, default):
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default
