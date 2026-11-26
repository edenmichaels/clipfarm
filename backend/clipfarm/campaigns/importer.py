"""Campaign importing, behind one interface (CampaignSource) so an official API can slot in later.

Sources today (all user-initiated; Whop / Content Rewards terms):
  PageSource   the page the user has open in ClipFarm's in-app browser, sent when they press
               "Import this page". Only that page's text and its campaign links are read.
  PasteSource  page text the user copied (Ctrl+A, Ctrl+C) and pasted in.
  ApiSource    placeholder for an official clipper API, if Whop / Content Rewards ever publish one.

There is NO background scraping, crawling or scheduled fetching of Whop or Content Rewards: nothing
in ClipFarm downloads their pages, and nothing clicks Join or Submit. Imports only parse text handed in.

Extraction = deterministic rules (parse) + the LLM (Ollama or the configured endpoint) for fields
the rules missed. Every LLM value is checked against the page text; anything it can't back up is
marked "needs confirming". Existing campaigns are updated with a list of what changed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from dataclasses import dataclass, field
from typing import Protocol

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
    if re.search(r"campaign (?:has )?ended|no longer accepting|not accepting clips|budget (?:is )?(?:exhausted|spent)"
                 r"|^ended$", t, re.I | re.M):
        out["status"] = "ended"
    if re.search(r"original audio only|no (?:background )?music|without (?:background )?music|"
                 r"do(?:n't| not) add (?:any )?music|keep (?:the )?original audio", t, re.I):
        put("no_music", 1)
    # Joined? A campaign page shows "Joined" + "Submit clip" once you're in, "Join Campaign" before.
    if re.search(r"^\+?\s*Joined$|Submit clip", t, re.M):
        out["joined"] = True
    elif re.search(r"^Join Campaign$", t, re.M):
        out["joined"] = False
    if re.search(r"(?:not available|unavailable|not open) (?:to|in) (?:australia|your country)|"
                 r"(?:us|usa|united states|uk) (?:clippers|creators)? ?only", t, re.I):
        put("pays_au", 0)
    elif re.search(r"\baustralia(?:n)?\b.*\b(?:eligible|welcome|accepted)\b|worldwide|all countries", t, re.I):
        put("pays_au", 1)

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
        if src == "llm" and sources.get(f) in ("page", "import", "api"):
            continue                     # a guess never replaces a value read from the page
        v = parsed[f]
        values[f] = json.dumps(v) if isinstance(v, (list, dict)) else v
        sources[f] = src
    for f in ("streamer_label", "clippers_joined", "rules", "status"):
        if f in parsed and (not row or sources.get(f) != "user"):
            values[f] = parsed[f]
    if parsed.get("joined") is True:
        values["joined"] = 1
        if not row or not row.get("joined_at"):
            values["joined_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    elif parsed.get("joined") is False and (not row or not row.get("joined")):
        values["joined"] = 0
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
        joined = bool(parsed["joined"]) if parsed.get("joined") is not None else joined
        base = {"name": parsed.get("name") or "Imported campaign", "operator": operator,
                "url": f"https://contentrewards.com/c/campaigns/{ext}" if ext and operator == "content_rewards" else None,
                "external_id": ext, "required_credit": "{url}", "currency": "USD", "source": "manual",
                "active": 1, "status": parsed.get("status", "active"), "joined": int(joined),
                "joined_at": datetime.now(timezone.utc).strftime("%Y-%m-%d") if joined else None,
                "fee": 0.10, "approval_rate": 0.60, "pays_au": 1,
                "allowed_platforms": json.dumps([]), "required_tags": json.dumps([])}
        base.update({k: v for k, v in values.items() if k not in ("joined", "joined_at")})
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


# ======================================================================================
# Sources, LLM extraction, list pages, change tracking
# ======================================================================================
SUMMARY_FIELDS = {     # field -> how to describe a change
    "payout_per_1k": ("Rate", lambda v: f"${float(v):.2f}/1K"),
    "min_payout": ("Min payout", lambda v: f"${float(v):g}"),
    "max_payout": ("Max payout", lambda v: f"${float(v):g}"),
    "budget_remaining": ("Budget left", lambda v: f"${float(v):,.0f}"),
    "budget_total": ("Budget", lambda v: f"${float(v):,.0f}"),
    "pool_size": ("Prize pool", lambda v: f"${float(v):,.0f}"),
    "view_threshold": ("Views to qualify", lambda v: f"{int(float(v)):,}"),
    "min_views": ("Min views", lambda v: f"{int(float(v)):,}"),
    "ends_at": ("Ends", lambda v: str(v)[:10]),
    "allowed_platforms": ("Platforms", lambda v: ", ".join(json.loads(v) if isinstance(v, str) else v)),
    "required_tags": ("Hashtags", lambda v: " ".join(json.loads(v) if isinstance(v, str) else v)),
    "status": ("Status", str),
    "joined": ("Joined", lambda v: "yes" if int(v) else "no"),
    "campaign_type": ("Type", lambda v: "prize pool" if v == "prize_pool" else "pay per view"),
}


@dataclass
class PageInput:
    """What the in-app browser hands over when the user presses "Import this page"."""
    url: str
    text: str
    title: str = ""
    links: list[dict] = field(default_factory=list)   # [{"href": ".../c/campaigns/<id>", "text": card text}]


class CampaignSource(Protocol):
    name: str

    def campaigns(self) -> list[dict]:
        """Parsed campaigns (same shape as parse()), each with "_sources" for the fields found."""
        ...


class PageSource:
    name = "in_app_browser"

    def __init__(self, page: PageInput, use_llm: bool = True):
        self.page, self.use_llm = page, use_llm

    def campaigns(self) -> list[dict]:
        cards = [l for l in self.page.links if _CAMPAIGN_ID.search(l.get("href", ""))]
        uniq: dict[str, dict] = {}
        for c in cards:
            uniq.setdefault(_CAMPAIGN_ID.search(c["href"]).group(1), c)
        single = _CAMPAIGN_ID.search(self.page.url or "")
        if len(uniq) >= 2 and not single:
            # A list of campaigns (Discover, search results): each card is parsed on its own.
            out = []
            for cid, c in uniq.items():
                p = extract(c.get("text", ""), c["href"], use_llm=False)
                if not p.get("name"):
                    first = next((l.strip() for l in c.get("text", "").splitlines()
                                  if l.strip() and not re.match(r"^[\d$#.,]|^(joined|ended)$", l.strip(), re.I)), None)
                    if first:
                        p["name"] = first[:120]
                if p.get("_sources") or p.get("name"):
                    p["external_id"] = cid
                    out.append(p)
            return out
        p = extract(self.page.text, self.page.url, use_llm=self.use_llm)
        return [p] if (p.get("_sources") or p.get("external_id")) else []


class PasteSource(PageSource):
    name = "paste"

    def __init__(self, text: str, url: str | None = None, use_llm: bool = True):
        super().__init__(PageInput(url=url or "", text=text), use_llm)


class ApiSource:
    """Official clipper API (Whop / Content Rewards have none as of Nov 2026). Implement campaigns()
    here when one exists; everything downstream (upsert, diffs, matching, projections) stays the same."""
    name = "official_api"

    def campaigns(self) -> list[dict]:
        raise NotImplementedError("No official Content Rewards / Whop clipper API yet.")


_CAMPAIGN_ID = re.compile(r"/c/campaigns/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")

LLM_FIELDS = ["name", "streamer_label", "campaign_type", "payout_per_1k", "min_views", "min_payout", "max_payout",
              "pool_size", "view_threshold", "budget_remaining", "budget_total", "ends_at", "allowed_platforms",
              "required_tags", "required_credit", "disclosure", "required_onscreen", "pays_au"]
LLM_SYSTEM = (
    "You read a clipping-campaign web page and return JSON with these keys (null when the page doesn't say): "
    "name, streamer_label, campaign_type ('pay_per_view' or 'prize_pool'), payout_per_1k (USD per 1000 views), "
    "min_views (per clip), min_payout (USD per clip), max_payout (USD per clip), pool_size (USD), view_threshold "
    "(total views to qualify for a pool), budget_remaining (USD), budget_total (USD), ends_at (YYYY-MM-DD), "
    "allowed_platforms (subset of tiktok, youtube_shorts, instagram_reels, x), required_tags (list of hashtags), "
    "required_credit, disclosure, required_onscreen (subset of streamer_overlay, streamer_face, watermark), "
    "pays_au (true/false only if the page says which countries are paid). Copy numbers exactly as written. "
    "Never guess.")


def extract(text: str, url: str | None, use_llm: bool = True) -> dict:
    """Rules first, then the LLM for whatever the rules missed. LLM values that can't be found in the
    page text are kept for projections but marked as needing confirmation."""
    p = parse(text, url)
    if not use_llm or not text.strip():
        return p
    try:
        from ..llm.client import complete_json
        llm = complete_json(LLM_SYSTEM, text[:12000], timeout=45)
    except Exception:
        return p        # no LLM: the rules' result stands
    unsure = []
    for f in LLM_FIELDS:
        v = llm.get(f)
        if v in (None, "", [], {}) or f in p.get("_sources", {}) or (f in ("name", "streamer_label") and p.get(f)):
            continue
        v = _normalise(f, v)
        if v is None:
            continue
        p[f] = v
        if f in ("name", "streamer_label"):
            continue
        if _supported(f, v, text):
            p["_sources"][f] = "import"
        else:
            p["_sources"][f] = "llm"      # used in projections, but stays "needs confirming"
            unsure.append(f)
    if unsure:
        p["_unsure"] = unsure            # not marked as filled -> stays in "needs confirming"
    return p


def _normalise(f: str, v):
    try:
        if f in ("payout_per_1k", "min_payout", "max_payout", "pool_size", "budget_remaining", "budget_total"):
            return float(str(v).replace("$", "").replace(",", ""))
        if f in ("min_views", "view_threshold"):
            return _count(str(v)) if not isinstance(v, (int, float)) else int(v)
        if f in ("allowed_platforms", "required_tags", "required_onscreen"):
            return [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x]
        if f == "pays_au":
            return 1 if v in (True, "true", "yes", 1) else 0 if v in (False, "false", "no", 0) else None
        if f == "campaign_type":
            return v if v in ("pay_per_view", "prize_pool") else None
        if f == "ends_at":
            return datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime("%Y-%m-%d 23:59:59")
        return str(v)
    except (ValueError, TypeError):
        return None


MONEY_FIELDS = {"payout_per_1k", "min_payout", "max_payout", "pool_size", "budget_remaining", "budget_total"}


def _supported(f: str, v, text: str) -> bool:
    """Is the LLM's value actually on the page? Field-specific checks first, then exact numbers
    (with word boundaries, and a $ in front for money)."""
    low = text.lower()
    if f == "pays_au":
        return bool(re.search(r"australia|worldwide|all countries|global", low))
    if f == "allowed_platforms":
        names = {"tiktok": r"tiktok", "youtube_shorts": r"youtube|shorts", "instagram_reels": r"instagram|reels",
                 "x": r"\bx\b|twitter"}
        return all(re.search(names.get(p, re.escape(p)), low) for p in v)
    if f == "required_tags":
        return all(t.lower().lstrip("#") in low for t in v)
    if f == "required_onscreen":
        keys = {"streamer_overlay": "overlay", "streamer_face": "face", "watermark": "watermark"}
        return all(keys.get(x, x) in low for x in v)
    if f == "ends_at":
        d = datetime.strptime(v[:10], "%Y-%m-%d")
        forms = {d.strftime("%b %d"), d.strftime("%B %d"), d.strftime("%b ") + str(d.day),
                 d.strftime("%B ") + str(d.day), v[:10], f"{d.day}/{d.month}", f"{d.day} {d.strftime('%b')}"}
        return any(re.search(rf"\b{re.escape(x.lower())}\b", low) for x in forms)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = float(v)
        forms = {f"{n:g}", f"{n:,.0f}", f"{n:.2f}", f"{n:,.2f}", f"{n:.1f}"}
        if n >= 1000:
            forms |= {f"{n / 1000:g}k", f"{n / 1000:g} k", f"{n / 1000:.1f}k", f"{n / 1e6:g}m"}
        pre = r"\$\s?" if f in MONEY_FIELDS else r"(?<![\d.,])"
        return any(re.search(pre + re.escape(x.lower()) + r"(?![\d])", low) for x in forms)
    return str(v).lower()[:40] in low


def _snapshot(pid: int) -> dict:
    return dict(db.q1("SELECT * FROM programs WHERE id=?", (pid,)) or {})


def describe_changes(old: dict, new: dict) -> list[str]:
    out = []
    for f, (label, fmt) in SUMMARY_FIELDS.items():
        a, b = old.get(f), new.get(f)
        if a == b or b is None:
            continue
        try:
            if a is not None and isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(float(a) - float(b)) < 1e-9:
                continue
            out.append(f"{label}: {fmt(a)} → {fmt(b)}" if a not in (None, "", "[]") else f"{label}: {fmt(b)}")
        except (TypeError, ValueError):
            out.append(f"{label} changed")
    return out


@dataclass
class ImportSummary:
    new: list[dict] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)
    unchanged: list[dict] = field(default_factory=list)
    ended: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.new) + len(self.updated) + len(self.unchanged)

    def text(self) -> str:
        if not self.total:
            return "No campaigns found on this page."
        parts = [f"{len(self.new)} new", f"{len(self.updated)} updated"]
        if self.unchanged:
            parts.append(f"{len(self.unchanged)} unchanged")
        if self.ended:
            parts.append(f"{len(self.ended)} ended")
        return f"Imported {self.total} campaign{'s' if self.total != 1 else ''}: " + ", ".join(parts) + "."

    def as_dict(self) -> dict:
        return {"summary": self.text(), "new": self.new, "updated": self.updated, "unchanged": self.unchanged,
                "ended": self.ended, "total": self.total}


def run_import(source: CampaignSource, operator: str = "content_rewards") -> ImportSummary:
    """Upsert everything a source returns and record what changed."""
    summary = ImportSummary()
    for parsed in source.campaigns():
        existing = None
        if parsed.get("external_id"):
            existing = db.q1("SELECT id FROM programs WHERE operator=? AND external_id=?", (operator, parsed["external_id"]))
        if not existing and parsed.get("name"):
            existing = db.q1("SELECT id FROM programs WHERE name=?", (parsed["name"],))
        before = _snapshot(existing["id"]) if existing else {}
        pid = upsert(parsed, operator, joined=False)
        after = _snapshot(pid)
        unsure = parsed.get("_unsure", [])
        changes = describe_changes(before, after) if before else []
        db.ex("UPDATE programs SET last_changes=?, import_source=?, last_imported_at=datetime('now') WHERE id=?",
              (json.dumps(changes), source.name, pid))
        item = {"id": pid, "name": after["name"], "changes": changes, "unsure": unsure,
                "needs_confirming": json.loads(after["needs_confirming"] or "[]"), "joined": bool(after["joined"])}
        if not before:
            summary.new.append(item)
        elif changes:
            summary.updated.append(item)
        else:
            summary.unchanged.append(item)
        if after.get("status") == "ended" and before.get("status") != "ended":
            summary.ended.append(item)
    db.log("info", "campaigns", f"{summary.text()} (source: {source.name})")
    return summary
