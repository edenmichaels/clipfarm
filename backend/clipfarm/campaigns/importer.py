"""Import a campaign from its page text.

Content Rewards (and most rewards programs) have no public API, and scraping them on a schedule
would break their terms. So campaigns come in by the user copying the campaign page (Ctrl+A, Ctrl+C
in their signed-in browser) and pasting it into ClipFarm, or by typing the fields in. Fields the
parser finds are marked as filled from the page; the rest are flagged "needs confirming".
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .. import db
from .fields import needs_confirming

PLATFORM_HEADERS = {"tiktok": "tiktok", "instagram": "instagram_reels", "youtube": "youtube_shorts", "x": "x",
                    "twitter": "x"}
_MONEY = r"\$\s?([\d,]+(?:\.\d+)?)\s*([KkMm])?"


def _money(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(_MONEY, s)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return v * {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1)


def _count(s: str) -> int | None:
    m = re.search(r"([\d.,]+)\s*([KkMm])?", s)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return int(v * {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1))


def parse(text: str, url: str | None = None) -> dict:
    """Pull every campaign field we can find out of pasted page text."""
    t = text.replace("⁦", "").replace("⁩", "")      # bidi marks the site wraps numbers in
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    out: dict = {"_sources": {}}

    def put(field, value):
        if value not in (None, [], ""):
            out[field] = value
            out["_sources"][field] = "import"

    # --- id / title
    m = re.search(r"campaigns/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", (url or "") + "\n" + t)
    if m:
        out["external_id"] = m.group(1)
    title = next((l for l in lines if re.search(r"campaign|clipping|\bpool\b|marathon", l, re.I)
                  and 8 < len(l) < 120 and not re.search(r"^(back|join campaign|\$[\d.,]+k?\s*pool)$", l, re.I)), None)
    if title:
        out["name"] = title

    # --- type: pools say "Split by views" / "share the pool"
    pool = re.search(r"Reach\s+([\d.,]+\s*[KkMm]?)\s+total views", t)
    if re.search(r"split by views|share the pool|\bpool\b", t, re.I) and not re.search(r"Per 1K views", t):
        put("campaign_type", "prize_pool")
        size = re.search(r"Pool\s*\n?\s*" + _MONEY, t) or re.search(_MONEY + r"\s*pool", t, re.I)
        if size:
            put("pool_size", _money(size.group(0)))
        if pool:
            put("view_threshold", _count(pool.group(1)))
        per = re.search(r"Each clip needs\s+([\d.,]+\s*[KkMm]?)\s+views", t, re.I)
        if per:
            put("min_views", _count(per.group(1)))
    elif re.search(r"Per 1K views", t):
        put("campaign_type", "pay_per_view")

    # --- per-platform CPM blocks: "TikTok / Per 1K views / $1.00 / Min payout / $10.00 / Max payout / $300.00"
    platforms, rates, mins, maxs = [], [], [], []
    for i, l in enumerate(lines):
        key = l.lower()
        if key in PLATFORM_HEADERS and i + 2 < len(lines) and lines[i + 1].lower().startswith("per 1k"):
            platforms.append(PLATFORM_HEADERS[key])
            rates.append(_money(lines[i + 2]))
            for j in range(i + 3, min(i + 9, len(lines))):
                if lines[j].lower() == "min payout" and j + 1 < len(lines):
                    mins.append(_money(lines[j + 1]))
                if lines[j].lower() == "max payout" and j + 1 < len(lines):
                    maxs.append(_money(lines[j + 1]))
    if not rates:
        r = re.search(r"Per 1K views\s*\n?\s*" + _MONEY, t) or re.search(_MONEY + r"\s*(?:/|per)\s*1K", t, re.I)
        if r:
            rates.append(_money(r.group(0)))
        mn = re.search(r"Min payout\s*\n?\s*" + _MONEY, t)
        mx = re.search(r"Max payout\s*\n?\s*" + _MONEY, t)
        if mn:
            mins.append(_money(mn.group(0)))
        if mx:
            maxs.append(_money(mx.group(0)))
    if platforms:
        put("allowed_platforms", sorted(set(platforms)))
    if rates and rates[0] is not None:
        put("payout_per_1k", max(r for r in rates if r is not None))
    if mins:
        put("min_payout", min(v for v in mins if v is not None))
    if maxs:
        put("max_payout", max(v for v in maxs if v is not None))

    # --- budget "Budget / $472 / $1,000" (paid out so far / total)
    b = re.search(r"Budget\s*\n?\s*" + _MONEY + r"\s*/\s*" + _MONEY, t)
    if b:
        used = float(b.group(1).replace(",", "")) * {"k": 1e3, "m": 1e6}.get((b.group(2) or "").lower(), 1)
        total = float(b.group(3).replace(",", "")) * {"k": 1e3, "m": 1e6}.get((b.group(4) or "").lower(), 1)
        put("budget_total", total)
        put("budget_remaining", max(0.0, total - used))
    left = re.search(_MONEY + r"\s+still up for grabs", t)
    if left and "budget_remaining" not in out:
        put("budget_remaining", _money(left.group(0)))

    # --- rules: hashtags, on-screen elements, credit, disclosure, dates
    tags = sorted(set(re.findall(r"(?<![\w/])#([A-Za-z][A-Za-z0-9_]{2,})", t)))
    if tags:
        put("required_tags", ["#" + x for x in tags])
    onscreen = []
    if re.search(r"overlay", t, re.I):
        onscreen.append("streamer_overlay")
    if re.search(r"must (?:show|include|feature)\s+(?:the\s+)?(?:streamer|him|her|them|[A-Z][a-z]+)", t):
        onscreen.append("streamer_face")
    if re.search(r"watermark", t, re.I):
        onscreen.append("watermark")
    if onscreen:
        put("required_onscreen", onscreen)
    if re.search(r"#ad\b|paid partnership|sponsored", t, re.I):
        put("disclosure", "#ad")
    end = re.search(r"(?:ends?|ending)\s+(?:on\s+)?([A-Z][a-z]{2,8}\s+\d{1,2}(?:,\s*\d{4})?)", t)
    if end:
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d", "%B %d"):
            try:
                d = datetime.strptime(end.group(1), fmt)
                if d.year == 1900:
                    d = d.replace(year=datetime.now().year)
                put("ends_at", d.strftime("%Y-%m-%d 23:59:59"))
                break
            except ValueError:
                continue
    if re.search(r"campaign (?:has )?ended|no longer accepting|budget (?:is )?(?:exhausted|spent)", t, re.I):
        out["status"] = "ended"

    # --- channels named on the page
    hints = {}
    for plat, rx in (("twitch", r"twitch\.tv/([A-Za-z0-9_]{3,25})"), ("kick", r"kick\.com/([A-Za-z0-9_\-]{3,25})"),
                     ("youtube", r"youtube\.com/(@[A-Za-z0-9_.\-]{3,30})")):
        m = re.search(rx, t)
        if m:
            hints[plat] = {"value": m.group(1), "source": "page"}
    if hints:
        out["channel_hints"] = hints
    # streamer name: "Clip <Name>'s ..." or "<Name> - $10,000 ..."
    m = re.search(r"\b(?i:clip)(?: content from)?\s+([A-Z][\w.]+(?:\s[A-Z][\w.]+)?)['’]s", t) or \
        re.search(r"^(?:\$[\d,.]+[Kk]?\s+)?([A-Z][\w.]+(?:\s[A-Z][\w.]+)?)\s*(?:[-–x×|]|GTA|Clipping)", title or "")
    if m:
        out["streamer_label"] = m.group(1).strip()
    joined = re.search(r"\n(\d{1,6})\n(?:\$[\d.,]+[Kk]? pool|CPM)", t)
    if joined:
        out["clippers_joined"] = int(joined.group(1))
    rules = "\n".join(l for l in lines if len(l) > 40)[:1500]
    if rules:
        out["rules"] = rules
    return out


def upsert(parsed: dict, operator: str = "content_rewards", joined: bool = True) -> int:
    """Create or update a campaign from parsed fields. Fields the user set by hand are never overwritten."""
    ext = parsed.get("external_id")
    row = None
    if ext:
        row = db.q1("SELECT * FROM programs WHERE operator=? AND external_id=?", (operator, ext))
    if not row and parsed.get("name"):
        row = db.q1("SELECT * FROM programs WHERE name=?", (parsed["name"],))
    sources = json.loads(row["field_sources"] or "{}") if row else {}
    values: dict = {}
    for f, src in parsed.get("_sources", {}).items():
        if sources.get(f) == "user":
            continue                     # the user's own value wins
        v = parsed[f]
        values[f] = json.dumps(v) if isinstance(v, (list, dict)) else v
        sources[f] = src
    for f in ("streamer_label", "clippers_joined", "rules", "status"):
        if f in parsed and (not row or sources.get(f) != "user"):
            values[f] = parsed[f]
    if "channel_hints" in parsed:
        old = json.loads(row["channel_hints"] or "{}") if row else {}
        values["channel_hints"] = json.dumps({**old, **parsed["channel_hints"]})
    values["field_sources"] = json.dumps(sources)
    values["last_synced_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if row:
        sets = ", ".join(f"{k}=?" for k in values)
        db.ex(f"UPDATE programs SET {sets} WHERE id=?", (*values.values(), row["id"]))
        pid = row["id"]
    else:
        base = {"name": parsed.get("name") or "Imported campaign", "operator": operator,
                "url": f"https://contentrewards.com/c/campaigns/{ext}" if ext and operator == "content_rewards" else None,
                "external_id": ext, "required_credit": "{url}", "currency": "USD", "source": "manual",
                "active": 1, "status": parsed.get("status", "active"), "joined": int(joined),
                "joined_at": datetime.now(timezone.utc).strftime("%Y-%m-%d") if joined else None,
                "fee": 0.10, "approval_rate": 0.60, "pays_au": 1,
                "allowed_platforms": json.dumps([]), "required_tags": json.dumps([])}
        base.update(values)
        cols = ", ".join(base)
        pid = db.ex(f"INSERT INTO programs({cols}, verified_at) VALUES ({', '.join('?' * len(base))}, datetime('now'))",
                    tuple(base.values()))
    full = db.q1("SELECT * FROM programs WHERE id=?", (pid,))
    db.ex("UPDATE programs SET needs_confirming=? WHERE id=?", (json.dumps(needs_confirming(full)), pid))
    return pid


def set_fields(program_id: int, fields: dict) -> None:
    """User edits: mark each edited field as confirmed by the user."""
    row = db.q1("SELECT * FROM programs WHERE id=?", (program_id,))
    sources = json.loads(row["field_sources"] or "{}")
    cols = {r["name"] for r in db.q("PRAGMA table_info(programs)")}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in cols or k in ("id", "field_sources", "needs_confirming"):
            continue
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
        sources[k] = "user"
    if sets:
        db.ex(f"UPDATE programs SET {', '.join(sets)}, field_sources=?, verified_at=datetime('now') WHERE id=?",
              (*vals, json.dumps(sources), program_id))
    full = db.q1("SELECT * FROM programs WHERE id=?", (program_id,))
    db.ex("UPDATE programs SET needs_confirming=? WHERE id=?", (json.dumps(needs_confirming(full)), program_id))
