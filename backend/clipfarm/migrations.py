"""Schema migrations. Additive only: columns and tables are added, nothing is dropped or rewritten,
so an existing %APPDATA%\\ClipFarm\\clipfarm.db keeps every row.

Each migration is idempotent (checks before it adds) and runs inside one transaction, then bumps
schema_version. `run()` is called by db.init_db() on every start.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Callable


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _script(conn: sqlite3.Connection, sql: str) -> None:
    """Like executescript, but inside the current transaction (executescript commits first)."""
    for stmt in sql.split(";"):
        if stmt.strip() and not all(l.strip().startswith("--") or not l.strip() for l in stmt.splitlines()):
            conn.execute(stmt)


def _add(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    if col not in _cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


# ---- v2: campaigns, projections, auto-pilot, submissions ------------------------------------
def _v2(conn: sqlite3.Connection) -> None:
    for col, decl in [
        ("campaign_type", "TEXT"),            # pay_per_view | prize_pool
        ("streamer_label", "TEXT"),           # streamer name as the campaign shows it
        ("pool_size", "REAL"),
        ("view_threshold", "INTEGER"),        # prize pool: total views needed to qualify
        ("budget_total", "REAL"),
        ("budget_remaining", "REAL"),
        ("ends_at", "TEXT"),
        ("min_payout", "REAL"),               # per-clip minimum payout (USD)
        ("required_onscreen", "TEXT"),        # JSON list e.g. ["streamer_overlay","streamer_face"]
        ("pays_au", "INTEGER"),               # 1 yes, 0 no, NULL unknown
        ("needs_confirming", "TEXT"),         # JSON list of field names not filled automatically or by the user
        ("field_sources", "TEXT"),            # JSON {field: "page"|"user"|"seed"|"default"}
        ("external_id", "TEXT"),              # campaign id on the rewards site
        ("status", "TEXT DEFAULT 'active'"),  # active | ending | out_of_budget | ended | paused
        ("joined", "INTEGER DEFAULT 0"),
        ("joined_at", "TEXT"),
        ("clippers_joined", "INTEGER"),
        ("channel_hints", "TEXT"),            # JSON {"twitch": "login", "youtube": "@handle", "kick": "slug"}
        ("fee", "REAL"),                      # platform fee on payouts (0.10 = 10 %)
        ("approval_rate", "REAL"),            # share of submitted clips approved
        ("last_synced_at", "TEXT"),
    ]:
        _add(conn, "programs", col, decl)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_programs_external ON programs(operator, external_id)")
    # Programs that already exist were added by the user as ones they joined; keep them working.
    conn.execute("UPDATE programs SET joined=1, field_sources=COALESCE(field_sources, '{}') WHERE joined=0 OR joined IS NULL")

    for col, decl in [("match_confidence", "REAL"), ("matched_by", "TEXT"), ("confirmed", "INTEGER DEFAULT 0")]:
        _add(conn, "streamer_programs", col, decl)
    # Existing links were made by hand in the streamer list: they count as confirmed.
    conn.execute("UPDATE streamer_programs SET confirmed=1, matched_by=COALESCE(matched_by,'Linked by you') "
                 "WHERE confirmed=0 OR confirmed IS NULL")

    for col, decl in [("oauth_status", "TEXT DEFAULT 'connected'"), ("followers", "INTEGER"),
                      ("max_posts_per_day", "INTEGER")]:
        _add(conn, "accounts", col, decl)

    for col, decl in [("program_id", "INTEGER"), ("projected_aud", "REAL"), ("manual", "INTEGER DEFAULT 0")]:
        _add(conn, "posts", col, decl)

    for col, decl in [("rules_applied", "TEXT"), ("review_flags", "TEXT"), ("caption_text", "TEXT"),
                      ("needs_payout_confirm", "INTEGER DEFAULT 0")]:
        _add(conn, "clips", col, decl)

    _add(conn, "payouts", "amount_aud", "REAL")

    _script(conn, """
    CREATE TABLE IF NOT EXISTS campaign_matches (
        id               INTEGER PRIMARY KEY,
        program_id       INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
        platform         TEXT NOT NULL,
        query            TEXT NOT NULL,
        candidate_login  TEXT,
        candidate_user_id TEXT,
        display_name     TEXT,
        confidence       REAL NOT NULL DEFAULT 0,
        reason           TEXT,
        status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','rejected')),
        created_at       TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (program_id, platform, candidate_user_id)
    );

    CREATE TABLE IF NOT EXISTS projections (
        streamer_id      INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
        program_id       INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
        platform         TEXT NOT NULL,
        per_clip_low     REAL, per_clip_likely REAL, per_clip_high REAL,
        per_day_low      REAL, per_day_likely  REAL, per_day_high  REAL,
        d7_low           REAL, d7_likely       REAL, d7_high       REAL,
        d30_low          REAL, d30_likely      REAL, d30_high      REAL,
        aud_per_pc_hour  REAL,
        clips_per_day    REAL,
        warnings         TEXT,              -- JSON list (e.g. fields that need confirming)
        details          TEXT,              -- JSON breakdown for "why this is ranked here"
        computed_at      TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (streamer_id, program_id, platform)
    );

    CREATE TABLE IF NOT EXISTS autopilot_log (
        id              INTEGER PRIMARY KEY,
        ts              TEXT NOT NULL DEFAULT (datetime('now')),
        action          TEXT NOT NULL,       -- select | switch | drop | pause | limit
        from_program_id INTEGER,
        to_program_id   INTEGER,
        streamer_id     INTEGER,
        reason          TEXT NOT NULL
    );

    -- "Submit to <campaign> on Content Rewards" checklist. One row per post: a post can never be
    -- submitted twice.
    CREATE TABLE IF NOT EXISTS submissions (
        id          INTEGER PRIMARY KEY,
        post_id     INTEGER NOT NULL UNIQUE REFERENCES posts(id) ON DELETE CASCADE,
        program_id  INTEGER NOT NULL REFERENCES programs(id),
        post_url    TEXT,
        status      TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','done')),
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        done_at     TEXT
    );

    CREATE TABLE IF NOT EXISTS health_checks (
        id         TEXT PRIMARY KEY,
        status     TEXT NOT NULL,            -- green | yellow | red
        title      TEXT NOT NULL,
        message    TEXT NOT NULL,
        fix        TEXT,                     -- fix action id, if one-click fixable
        checked_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)
    # Never post the same rendered video twice to one account (content hash, not just clip id).
    conn.execute("CREATE INDEX IF NOT EXISTS ix_posts_account_time ON posts(account_id, scheduled_at)")


# ---- v3: seed data (Eden's joined campaigns, posting accounts) -------------------------------
def _v3(conn: sqlite3.Connection) -> None:
    from .campaigns.seed import seed
    seed(conn)


# ---- v4: page import, remembered match answers, per-account platforms ------------------------
def _v4(conn: sqlite3.Connection) -> None:
    _add(conn, "programs", "last_changes", "TEXT")       # JSON list: "Rate: $1.00/1K → $1.50/1K"
    _add(conn, "programs", "import_source", "TEXT")      # in_app_browser | paste | manual | seed | official_api
    _add(conn, "programs", "last_imported_at", "TEXT")
    _add(conn, "streamer_programs", "decided_by_user", "INTEGER DEFAULT 0")
    _script(conn, """
    -- Answers to "Is this the right campaign?" -- remembered forever, never asked again.
    CREATE TABLE IF NOT EXISTS match_decisions (
        id          INTEGER PRIMARY KEY,
        streamer_id INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
        program_id  INTEGER REFERENCES programs(id) ON DELETE CASCADE,   -- NULL = "none of these"
        decision    TEXT NOT NULL CHECK (decision IN ('accepted','rejected','none')),
        decided_at  TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (streamer_id, program_id)
    );
    CREATE INDEX IF NOT EXISTS ix_match_decisions_streamer ON match_decisions(streamer_id)
    """)
    # Links the user already confirmed in v0.2 count as answered.
    conn.execute("""INSERT OR IGNORE INTO match_decisions(streamer_id, program_id, decision)
                    SELECT streamer_id, program_id, 'accepted' FROM streamer_programs WHERE confirmed=1""")
    conn.execute("""INSERT OR IGNORE INTO match_decisions(streamer_id, program_id, decision)
                    SELECT s.id, cm.program_id, 'rejected' FROM campaign_matches cm
                    JOIN streamers s ON s.platform=cm.platform AND s.platform_user_id=cm.candidate_user_id
                    WHERE cm.status='rejected'""")
    conn.execute("UPDATE programs SET import_source=COALESCE(import_source, source)")


# ---- v5: captions, highlight types + feedback, music library, trending sounds ------------------
def _v5(conn: sqlite3.Connection) -> None:
    for col, decl in [
        ("words", "TEXT"),                 # JSON transcript words (editable in Review)
        ("caption_preset", "TEXT"),
        ("caption_nudge", "REAL DEFAULT 0"),
        ("moment_type", "TEXT"),           # Funny | Action/Explosion | Rage/Reaction | Clutch/Skill | Fail | Wholesome
        ("moment_reason", "TEXT"),
        ("music_track_id", "INTEGER"),
        ("music_plan", "TEXT"),            # JSON {offset, entry, beats, mode}
        ("original_audio_only", "INTEGER DEFAULT 0"),
        ("base_path", "TEXT"),             # pass-1 render, for fast re-renders
        ("nomusic_path", "TEXT"),          # "for trending sound" export
        ("render_spec", "TEXT"),           # JSON of what was rendered (to re-render the same way)
    ]:
        _add(conn, "clips", col, decl)
    for col, decl in [("type", "TEXT"), ("reason", "TEXT"), ("words", "TEXT"), ("components", "TEXT"),
                      ("detector", "TEXT")]:
        _add(conn, "moments", col, decl)
    _add(conn, "programs", "no_music", "INTEGER")      # 1 = "original audio only" / "no music" campaign
    _script(conn, """
    CREATE TABLE IF NOT EXISTS clip_feedback (
        clip_id    INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
        rating     INTEGER NOT NULL CHECK (rating IN (-1, 1)),
        tags       TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS music_tracks (
        id           INTEGER PRIMARY KEY,
        path         TEXT NOT NULL UNIQUE,
        title        TEXT NOT NULL,
        artist       TEXT,
        source       TEXT NOT NULL CHECK (source IN ('bundled','local','library')),
        licence      TEXT NOT NULL,
        licence_url  TEXT,
        attribution  TEXT,                 -- text to put in the description, if the licence needs it
        moods        TEXT NOT NULL DEFAULT '[]',
        mood_scores  TEXT,
        bpm          REAL,
        beats        TEXT,                 -- JSON beat times (s)
        drop_at      REAL,                 -- strongest downbeat after the build (s)
        loudness     REAL,                 -- integrated LUFS
        energy       REAL,                 -- 0..1
        instrumental INTEGER,
        duration     REAL,
        tags_edited  INTEGER NOT NULL DEFAULT 0,
        enabled      INTEGER NOT NULL DEFAULT 1,
        sha256       TEXT,
        added_at     TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS music_usage (
        id         INTEGER PRIMARY KEY,
        track_id   INTEGER NOT NULL REFERENCES music_tracks(id) ON DELETE CASCADE,
        clip_id    INTEGER REFERENCES clips(id) ON DELETE CASCADE,
        account_id INTEGER,
        used_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS ix_music_usage_account ON music_usage(account_id, used_at);
    -- Trending sound NAMES only (from your list or an official API). Never audio: you add the sound
    -- inside TikTok/Instagram's own editor when posting.
    CREATE TABLE IF NOT EXISTS trending_sounds (
        id        INTEGER PRIMARY KEY,
        name      TEXT NOT NULL,
        artist    TEXT,
        platform  TEXT NOT NULL DEFAULT 'tiktok',
        moods     TEXT NOT NULL DEFAULT '[]',
        url       TEXT,
        active    INTEGER NOT NULL DEFAULT 1,
        added_at  TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (name, platform)
    )
    """)
    # Campaigns whose rules say original audio only / no music.
    conn.execute("""UPDATE programs SET no_music=1 WHERE no_music IS NULL AND (
        lower(COALESCE(rules,'')) LIKE '%original audio%' OR lower(COALESCE(rules,'')) LIKE '%no music%'
        OR lower(COALESCE(rules,'')) LIKE '%no background music%' OR lower(COALESCE(rules,'')) LIKE '%without music%'
        OR lower(COALESCE(rules,'')) LIKE '%don''t add music%' OR lower(COALESCE(rules,'')) LIKE '%do not add music%')""")
    conn.execute("UPDATE moments SET detector=COALESCE(detector,'v1')")


# ---- v6: edit projects (EDL), version history, caption templates ---------------------------------
def _v6(conn: sqlite3.Connection) -> None:
    _script(conn, """
    CREATE TABLE IF NOT EXISTS edit_projects (
        id         INTEGER PRIMARY KEY,
        clip_id    INTEGER NOT NULL UNIQUE REFERENCES clips(id) ON DELETE CASCADE,
        data       TEXT NOT NULL,                 -- the edit decision list (JSON)
        head       INTEGER NOT NULL DEFAULT 1,    -- current version (undo/redo moves it)
        status     TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','edited','ready','posted')),
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS edit_versions (
        id         INTEGER PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES edit_projects(id) ON DELETE CASCADE,
        version    INTEGER NOT NULL,
        data       TEXT NOT NULL,
        label      TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (project_id, version)
    );
    CREATE TABLE IF NOT EXISTS caption_templates (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE,
        data       TEXT NOT NULL,                 -- template fields (font, colours, animation, ...)
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """)
    _add(conn, "clips", "source_path", "TEXT")    # kept source section, so the clip stays editable


# ---- v7: virality + projected earnings per clip, rule flags, trend topics, view predictions -------------
def _v7(conn: sqlite3.Connection) -> None:
    for col, decl in (("virality", "INTEGER"), ("virality_reason", "TEXT"), ("virality_parts", "TEXT"),
                      ("profit_low", "REAL"), ("profit_likely", "REAL"), ("profit_high", "REAL"),
                      ("profit_platform", "TEXT"), ("expected_views", "INTEGER"), ("rule_flags", "TEXT"),
                      ("scored_at", "TEXT"), ("deleted_at", "TEXT")):
        _add(conn, "clips", col, decl)
    _script(conn, """
    CREATE TABLE IF NOT EXISTS trend_topics(
        id INTEGER PRIMARY KEY, topic TEXT NOT NULL UNIQUE COLLATE NOCASE, active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS clip_signals(           -- implicit learning signals: keep/delete/edit/export
        id INTEGER PRIMARY KEY, clip_id INTEGER NOT NULL, kind TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE INDEX IF NOT EXISTS clip_signals_clip ON clip_signals(clip_id);
    CREATE INDEX IF NOT EXISTS clips_virality ON clips(virality);
    """)


# ---- v8: brand templates per account, clip variants (A/B) ------------------------------------------------
def _v8(conn: sqlite3.Connection) -> None:
    _script(conn, """
    CREATE TABLE IF NOT EXISTS brand_templates(
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, data TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    """)
    _add(conn, "accounts", "brand_template_id", "INTEGER")
    _add(conn, "clips", "variant_of", "INTEGER")
    _add(conn, "clips", "variant_label", "TEXT")


# ---- v9: failed sources show their error + a Retry button ------------------------------------------------
def _v9(conn: sqlite3.Connection) -> None:
    _add(conn, "sources", "error", "TEXT")
    _add(conn, "sources", "retry_at", "TEXT")
    _add(conn, "sources", "failed_at", "TEXT")


# ---- v10: viral moment model (examples from top Twitch clips etc., per-game models, moment tags) ---------
def _v10(conn: sqlite3.Connection) -> None:
    _script(conn, """
    CREATE TABLE IF NOT EXISTS viral_examples(
        id INTEGER PRIMARY KEY, game_id INTEGER, streamer_id INTEGER,
        source_kind TEXT NOT NULL,           -- twitch_clip | negative | mundane | my_post | feedback | style_ref | youtube_short
        platform_id TEXT, vod_id TEXT, vod_offset REAL, duration REAL, views INTEGER,
        label REAL NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
        features TEXT, tags TEXT, title TEXT, day TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(source_kind, platform_id, vod_offset));
    CREATE INDEX IF NOT EXISTS viral_examples_game ON viral_examples(game_id, day);
    CREATE TABLE IF NOT EXISTS viral_models(
        game_id INTEGER PRIMARY KEY, data TEXT NOT NULL, trained_at TEXT);
    """)
    for col, decl in (("viral_score", "INTEGER"), ("tags", "TEXT"), ("tag_timeline", "TEXT"), ("features", "TEXT"),
                      ("contributions", "TEXT")):
        _add(conn, "moments", col, decl)


# ---- v11: Trending Styles (saved styles) and your own style references (screenshots + notes) --------------
def _v11(conn: sqlite3.Connection) -> None:
    _script(conn, """
    CREATE TABLE IF NOT EXISTS styles(
        id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, data TEXT NOT NULL, ref_id INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')));
    CREATE TABLE IF NOT EXISTS style_refs(
        id INTEGER PRIMARY KEY, image_path TEXT, notes TEXT, tags TEXT, source_note TEXT, analysis TEXT,
        style_id INTEGER REFERENCES styles(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')));
    """)


# ---- v12: fixed framing + static captions (v0.8.5) --------------------------------------------------------
def _get(conn: sqlite3.Connection, key: str, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def _set(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, json.dumps(value)))


def _v12(conn: sqlite3.Connection) -> None:
    """Every edit project: pans / animated zooms / shakes -> one fixed crop per shot (with a one-line notice on
    the clip). Your saved styles: moving captions, shakes and animated zooms -> the static versions (listed once
    in the Styles tab). Facecam boxes saved automatically before are marked as guesses (a box you draw wins)."""
    from .edit import framing, styles
    notes = _get(conn, "edit_notices", {}) or {}
    for pid, cid, head, data in conn.execute("SELECT id, clip_id, head, data FROM edit_projects").fetchall():
        try:
            edl = json.loads(data)
        except ValueError:
            continue
        if edl.get("framing") == framing.MARK:
            continue
        new, changes = framing.convert(edl)
        blob = json.dumps(new)
        conn.execute("UPDATE edit_projects SET data=? WHERE id=?", (blob, pid))
        conn.execute("UPDATE edit_versions SET data=? WHERE project_id=? AND version=?", (blob, pid, head))
        if (msg := framing.notice(changes)):
            notes[str(cid)] = msg
    _set(conn, "edit_notices", notes)
    changed = []
    if "styles" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        for sid, name, data in conn.execute("SELECT id, name, data FROM styles").fetchall():
            try:
                old = json.loads(data)
            except ValueError:
                continue
            new, what = styles.convert_saved(old)
            if what:
                changed.append({"name": name, "changes": what})
            conn.execute("UPDATE styles SET data=?, updated_at=datetime('now') WHERE id=?", (json.dumps(new), sid))
    if changed:
        _set(conn, "style_notices", changed)
    for key, value in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'facecam:%'").fetchall():
        try:
            box = json.loads(value)
        except ValueError:
            continue
        if isinstance(box, dict) and "by" not in box:
            _set(conn, key, {**box, "by": "auto"})


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [(2, _v2), (3, _v3), (4, _v4), (5, _v5), (6, _v6),
                                                                      (7, _v7), (8, _v8), (9, _v9), (10, _v10),
                                                                      (11, _v11), (12, _v12)]
LATEST = MIGRATIONS[-1][0]


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def run(conn: sqlite3.Connection, upto: int | None = None) -> list[int]:
    """Apply pending migrations. Returns the versions applied."""
    applied = []
    for version, fn in MIGRATIONS:
        if upto is not None and version > upto:
            break
        if current_version(conn) >= version:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            fn(conn)
            conn.execute("UPDATE schema_version SET version=?", (version,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def j(value) -> str:
    return json.dumps(value)
