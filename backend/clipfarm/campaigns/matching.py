"""Link campaigns to streamer channels, and keep permissions in sync.

Confidence rules (auto-link at >= AUTO_LINK, otherwise ask the user):
  0.95  the campaign page names the channel (twitch.tv/x, youtube.com/@x, kick.com/x) and it exists
  0.80  the streamer's name matches a channel login/handle exactly        (needs confirming)
  +0.10 that channel is live in, or recently streamed, the campaign's game (GTA 6)
  0.60  best search result with a similar name                             (needs confirming)
Any streamer linked to an active campaign the user joined becomes permission_status='program_member'.
"""
from __future__ import annotations

import json
import re

from .. import db
from ..providers import StreamerHit, available_providers

AUTO_LINK = 0.9
NOTE = "Content Rewards campaign, joined Nov 2026"


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _existing(platform: str, value: str) -> dict | None:
    v = value.lstrip("@").lower()
    return db.q1("""SELECT * FROM streamers WHERE platform=? AND (lower(login)=? OR lower(ltrim(login,'@'))=?
                    OR platform_user_id=?)""", (platform, v, v, value))


def _upsert(h: StreamerHit) -> int:
    from ..discovery.service import upsert_streamer
    return upsert_streamer(h)


def _plays_game(streamer_id: int, game_name: str = "Grand Theft Auto VI") -> bool:
    return db.q1("""SELECT 1 FROM streamer_snapshots ss JOIN games g ON g.id=ss.game_id
                    WHERE ss.streamer_id=? AND g.name=? AND ss.taken_at >= datetime('now','-3 days')""",
                 (streamer_id, game_name)) is not None


def _lookup(provider_name: str, providers: dict, value: str) -> StreamerHit | None:
    p = providers.get(provider_name)
    if not p:
        return None
    if provider_name == "twitch":
        hits = p.users_by_login([value])
        return hits[0] if hits else None
    if provider_name == "youtube":
        return p.channel_by_handle(value)
    if provider_name == "kick":
        return p.channel_by_slug(value)
    return None


def link(program_id: int, streamer_id: int, confidence: float, matched_by: str, confirmed: bool) -> None:
    db.ex("""INSERT INTO streamer_programs(streamer_id, program_id, match_confidence, matched_by, confirmed)
             VALUES (?,?,?,?,?) ON CONFLICT(streamer_id, program_id) DO UPDATE SET
             match_confidence=excluded.match_confidence, matched_by=excluded.matched_by,
             confirmed=MAX(confirmed, excluded.confirmed)""",
          (streamer_id, program_id, confidence, matched_by, int(confirmed)))
    sync_permissions()


def match_program(program_id: int, providers: dict | None = None) -> dict:
    """Try to link one campaign. Returns {"linked": [...], "pending": [...], "waiting": reason|None}."""
    prog = db.q1("SELECT * FROM programs WHERE id=?", (program_id,))
    res = {"linked": [], "pending": [], "waiting": None}
    if not prog or not prog["streamer_label"]:
        return res      # e.g. a music/sound campaign: never linked to a streamer
    if db.q1("SELECT 1 FROM streamer_programs WHERE program_id=? AND confirmed=1", (program_id,)):
        return res      # already linked for sure
    hints = json.loads(prog["channel_hints"] or "{}")
    providers = providers if providers is not None else available_providers()

    candidates: list[tuple[float, str, int, str]] = []    # (confidence, platform, streamer_id, reason)
    for platform, h in hints.items():
        value, source = (h["value"], h.get("source", "name")) if isinstance(h, dict) else (h, "name")
        row = _existing(platform, value)
        sid = row["id"] if row else None
        if sid is None:
            try:
                hit = _lookup(platform, providers, value)
            except Exception as e:
                db.log("warn", "campaigns", f"Lookup of {platform}:{value} failed: {e}")
                hit = None
            if hit:
                sid = _upsert(hit)
            elif platform not in providers:
                res["waiting"] = f"Connect {platform.title()} in Settings so ClipFarm can find {value}."
                continue
            else:
                continue
        conf = 0.95 if source == "page" else 0.80
        reason = (f"{platform}:{value} is linked on the campaign page" if source == "page"
                  else f"{platform}:{value} matches the streamer name '{prog['streamer_label']}'")
        if conf < AUTO_LINK and _plays_game(sid):
            conf += 0.10
            reason += " and is streaming GTA 6"
        candidates.append((conf, platform, sid, reason))

    if not candidates and "twitch" in providers and not hints:
        try:
            for hit in providers["twitch"].search_channels(prog["streamer_label"], 5):
                if _norm(hit.display_name) == _norm(prog["streamer_label"]):
                    candidates.append((0.6, "twitch", _upsert(hit), f"Twitch search found '{hit.display_name}'"))
                    break
        except Exception as e:
            db.log("warn", "campaigns", f"Twitch search for {prog['streamer_label']} failed: {e}")

    for conf, platform, sid, reason in sorted(candidates, reverse=True):
        s = db.q1("SELECT * FROM streamers WHERE id=?", (sid,))
        if conf >= AUTO_LINK:
            link(program_id, sid, conf, reason, confirmed=True)
            res["linked"].append({"streamer_id": sid, "platform": platform, "login": s["login"], "reason": reason})
        else:
            db.ex("""INSERT INTO campaign_matches(program_id, platform, query, candidate_login, candidate_user_id,
                     display_name, confidence, reason) VALUES (?,?,?,?,?,?,?,?)
                     ON CONFLICT(program_id, platform, candidate_user_id) DO UPDATE SET confidence=excluded.confidence,
                     reason=excluded.reason""",
                  (program_id, platform, prog["streamer_label"], s["login"], s["platform_user_id"],
                   s["display_name"], conf, reason))
            res["pending"].append({"streamer_id": sid, "platform": platform, "login": s["login"], "reason": reason,
                                   "confidence": conf})
    return res


def match_all() -> dict:
    providers = available_providers()
    out = {}
    for p in db.q("SELECT id, name FROM programs WHERE active=1 AND joined=1 AND streamer_label IS NOT NULL"):
        out[p["name"]] = match_program(p["id"], providers)
    sync_permissions()
    return out


def confirm_match(match_id: int, accept: bool) -> None:
    m = db.q1("SELECT * FROM campaign_matches WHERE id=?", (match_id,))
    if not m:
        raise KeyError(match_id)
    db.ex("UPDATE campaign_matches SET status=? WHERE id=?", ("confirmed" if accept else "rejected", match_id))
    if accept:
        s = db.q1("SELECT id FROM streamers WHERE platform=? AND platform_user_id=?",
                  (m["platform"], m["candidate_user_id"]))
        if s:
            link(m["program_id"], s["id"], 1.0, f"Confirmed by you ({m['reason']})", confirmed=True)


def pending_matches() -> list[dict]:
    return db.q("""SELECT cm.*, p.name program_name FROM campaign_matches cm JOIN programs p ON p.id=cm.program_id
                   WHERE cm.status='pending' ORDER BY p.name, cm.confidence DESC""")


def sync_permissions() -> int:
    """Streamers linked (and confirmed) to an active campaign the user joined become program_member.
    Prohibited stays prohibited. Rows and history are never deleted."""
    rows = db.q("""SELECT DISTINCT s.id FROM streamers s
                   JOIN streamer_programs sp ON sp.streamer_id=s.id AND sp.confirmed=1
                   JOIN programs p ON p.id=sp.program_id
                   WHERE p.active=1 AND p.joined=1 AND COALESCE(p.status,'active') NOT IN ('ended')
                   AND s.permission_status IN ('unknown','allowed')""")
    for r in rows:
        db.ex("""UPDATE streamers SET permission_status='program_member',
                 permission_note=COALESCE(NULLIF(permission_note,''), ?), updated_at=datetime('now') WHERE id=?""",
              (NOTE, r["id"]))
    return len(rows)


def active_campaigns_for(streamer_id: int) -> list[dict]:
    """Joined, confirmed, still-running campaigns for a streamer (empty = 'no payout')."""
    return db.q("""SELECT p.* FROM programs p JOIN streamer_programs sp ON sp.program_id=p.id
                   WHERE sp.streamer_id=? AND sp.confirmed=1 AND p.active=1 AND p.joined=1
                   AND COALESCE(p.status,'active') IN ('active','ending')""", (streamer_id,))
