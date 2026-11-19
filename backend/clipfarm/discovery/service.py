"""Discovery: pull live streamers for a game from every connected platform, persist
snapshots, and compute rankings."""
from __future__ import annotations

import json
import time

from .. import db
from ..redact import redact
from ..providers import StreamerHit, available_providers
from .ranking import RankInput, clip_profit_score


def upsert_game(name: str, twitch_id: str | None = None, box_art_url: str | None = None,
                kick_slug: str | None = None) -> int:
    db.ex("""INSERT INTO games(name, twitch_id, youtube_query, kick_slug, box_art_url) VALUES (?,?,?,?,?)
             ON CONFLICT(name) DO UPDATE SET twitch_id=COALESCE(excluded.twitch_id, twitch_id),
               kick_slug=COALESCE(excluded.kick_slug, kick_slug),
               box_art_url=COALESCE(excluded.box_art_url, box_art_url), updated_at=datetime('now')""",
          (name, twitch_id, name, kick_slug, box_art_url))
    return db.q1("SELECT id FROM games WHERE name=?", (name,))["id"]


def upsert_streamer(h: StreamerHit) -> int:
    db.ex("""INSERT INTO streamers(platform, platform_user_id, login, display_name, avatar_url, followers)
             VALUES (?,?,?,?,?,?)
             ON CONFLICT(platform, platform_user_id) DO UPDATE SET login=excluded.login,
               display_name=excluded.display_name, avatar_url=COALESCE(excluded.avatar_url, avatar_url),
               followers=COALESCE(excluded.followers, followers), updated_at=datetime('now')""",
          (h.platform, h.platform_user_id, h.login, h.display_name, h.avatar_url, h.followers))
    return db.q1("SELECT id FROM streamers WHERE platform=? AND platform_user_id=?",
                 (h.platform, h.platform_user_id))["id"]


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def resolve_platform_ids(game_id: int, providers: dict | None = None) -> None:
    """A game picked from Twitch only had a Twitch id, so Kick discovery never ran (and vice versa).
    Look the game up by name on every connected platform and store the missing ids."""
    game = db.q1("SELECT * FROM games WHERE id=?", (game_id,))
    providers = providers if providers is not None else available_providers()
    want = {"twitch": "twitch_id", "kick": "kick_slug"}
    for name, col in want.items():
        if game[col] or name not in providers:
            continue
        try:
            hits = providers[name].search_games(game["name"])
        except Exception as e:
            db.log("warn", "discovery", f"{name}: couldn't look up '{game['name']}': {e}")
            continue
        exact = [h for h in hits if _norm(h.name) == _norm(game["name"])]
        if exact:
            db.ex(f"UPDATE games SET {col}=? WHERE id=?", (exact[0].platform_game_id, game_id))


def _youtube_due(game_id: int) -> bool:
    """search.list costs 100 of the 10,000 daily quota units, so YouTube discovery is cached."""
    key = f"yt_discovery_at:{game_id}"
    last = db.get_setting(key)
    mins = db.get_setting("ranking_refresh_minutes", 30)
    if last and time.time() - last < mins * 60:
        return False
    db.set_setting(key, time.time())
    return True


def discover(game_id: int, force_youtube: bool = False) -> list[dict]:
    """Query every connected platform for live streamers of `game_id` and refresh rankings."""
    providers = available_providers()
    resolve_platform_ids(game_id, providers)
    game = db.q1("SELECT * FROM games WHERE id=?", (game_id,))
    hits: list[StreamerHit] = []
    errors = []
    for name, p in providers.items():
        try:
            if name == "twitch" and game["twitch_id"]:
                hits += p.live_streamers(game["twitch_id"])
            elif name == "youtube" and (force_youtube or _youtube_due(game_id)):
                hits += p.live_streamers(game["youtube_query"] or game["name"])
            elif name == "kick" and game["kick_slug"]:
                hits += p.live_streamers(game["kick_slug"])
        except Exception as e:  # one platform failing must not kill discovery
            msg = redact(f"{name}: {e}")
            errors.append(msg)
            db.log("warn", "discovery", msg)
    with db.tx():
        for h in hits:
            sid = upsert_streamer(h)
            db.ex("""INSERT INTO streamer_snapshots(streamer_id, game_id, is_live, viewers, followers, stream_title)
                     VALUES (?,?,?,?,?,?)""", (sid, game_id, int(h.is_live), h.viewers, h.followers, h.title))
    rank_game(game_id)
    return errors


def _rank_input(streamer: dict, game_id: int) -> RankInput:
    sid = streamer["id"]
    stats = db.q1("""SELECT AVG(viewers) avg_v FROM streamer_snapshots
                     WHERE streamer_id=? AND taken_at >= datetime('now','-7 days')""", (sid,))
    # growth: last 24h avg vs previous 6 days avg
    recent = db.q1("""SELECT AVG(viewers) v FROM streamer_snapshots WHERE streamer_id=?
                      AND taken_at >= datetime('now','-1 day')""", (sid,))["v"]
    older = db.q1("""SELECT AVG(viewers) v FROM streamer_snapshots WHERE streamer_id=?
                     AND taken_at < datetime('now','-1 day') AND taken_at >= datetime('now','-7 days')""",
                  (sid,))["v"]
    growth = ((recent - older) / older) if (recent and older) else 0.0
    # Only active campaigns you've joined (and confirmed links) count as payout. Everyone else is "no payout".
    payout = db.q1("""SELECT MAX(p.payout_per_1k) best FROM programs p
                      JOIN streamer_programs sp ON sp.program_id=p.id AND sp.confirmed=1
                      WHERE sp.streamer_id=? AND p.active=1 AND p.joined=1
                      AND COALESCE(p.status,'active') IN ('active','ending')""", (sid,))["best"]
    projected = db.q1("SELECT MAX(aud_per_pc_hour) v FROM projections WHERE streamer_id=?", (sid,))["v"]
    clip_rate = db.q1("""SELECT COUNT(*) n FROM sources WHERE streamer_id=? AND kind IN ('community_clip','featured_clip')
                         AND created_at_platform >= datetime('now','-7 days')""", (sid,))["n"]
    hours_live = max(1.0, db.q1("""SELECT COUNT(*) n FROM streamer_snapshots WHERE streamer_id=? AND is_live=1
                                   AND taken_at >= datetime('now','-7 days')""", (sid,))["n"] * 0.5)
    prior = db.q1("SELECT competition_count FROM rankings WHERE streamer_id=? AND game_id=?", (sid, game_id))
    return RankInput(avg_viewers=stats["avg_v"] or 0, followers=streamer["followers"], payout_per_1k=payout,
                     permission_status=streamer["permission_status"],
                     competition_count=prior["competition_count"] if prior else None,
                     clips_per_stream_hour=clip_rate / hours_live, growth_pct=growth,
                     projected_aud_per_hour=projected if payout is not None or projected else None)


def rank_game(game_id: int) -> None:
    # Everyone seen in this game recently, plus every streamer with a campaign (no hard-coded list).
    streamers = db.q("""SELECT DISTINCT s.* FROM streamers s WHERE s.id IN (
                          SELECT streamer_id FROM streamer_snapshots WHERE game_id=? AND taken_at >= datetime('now','-7 days')
                          UNION SELECT streamer_id FROM streamer_programs WHERE confirmed=1)""", (game_id,))
    with db.tx():
        for s in streamers:
            r = clip_profit_score(_rank_input(s, game_id))
            db.ex("""INSERT INTO rankings(streamer_id, game_id, profit_score, audience_score, payout_score,
                       competition_score, highlight_score, earnings_tier, computed_at)
                     VALUES (?,?,?,?,?,?,?,?,datetime('now'))
                     ON CONFLICT(streamer_id, game_id) DO UPDATE SET profit_score=excluded.profit_score,
                       audience_score=excluded.audience_score, payout_score=excluded.payout_score,
                       competition_score=excluded.competition_score, highlight_score=excluded.highlight_score,
                       earnings_tier=excluded.earnings_tier, computed_at=excluded.computed_at""",
                  (s["id"], game_id, r["score"], r["audience"], r["payout"], r["competition"],
                   r["highlight"], r["tier"]))


def ranked_streamers(game_id: int) -> list[dict]:
    rows = db.q("""
        SELECT s.*, r.profit_score, r.audience_score, r.payout_score, r.competition_score,
               r.highlight_score, r.earnings_tier, r.competition_count,
               (SELECT viewers FROM streamer_snapshots WHERE streamer_id=s.id ORDER BY taken_at DESC LIMIT 1) AS current_viewers,
               (SELECT is_live FROM streamer_snapshots WHERE streamer_id=s.id ORDER BY taken_at DESC LIMIT 1) AS is_live,
               (SELECT taken_at FROM streamer_snapshots WHERE streamer_id=s.id ORDER BY taken_at DESC LIMIT 1) AS last_seen,
               (SELECT AVG(viewers) FROM streamer_snapshots WHERE streamer_id=s.id
                    AND taken_at >= datetime('now','-7 days')) AS avg_viewers
        FROM rankings r JOIN streamers s ON s.id=r.streamer_id
        WHERE r.game_id=? ORDER BY r.profit_score DESC""", (game_id,))
    for r in rows:
        # A snapshot older than ~1h means they're no longer live in our data.
        r["programs"] = db.q("""SELECT p.id, p.name, p.operator, p.payout_per_1k, p.currency, p.min_views,
                                       p.rules, p.url FROM programs p JOIN streamer_programs sp ON sp.program_id=p.id
                                WHERE sp.streamer_id=? AND p.active=1""", (r["id"],))
        r["best_platforms"] = json.loads(r["best_platforms"]) if r["best_platforms"] else []
        pj = db.q1("""SELECT program_id, platform, per_clip_low, per_clip_likely, per_clip_high, per_day_likely,
                      d7_likely, d30_likely, aud_per_pc_hour, warnings FROM projections WHERE streamer_id=?
                      ORDER BY aud_per_pc_hour DESC LIMIT 1""", (r["id"],))
        r["projection"] = pj
        r["no_payout"] = not any((p.get("payout_per_1k") or 0) > 0 for p in r["programs"]) and pj is None
    return rows
