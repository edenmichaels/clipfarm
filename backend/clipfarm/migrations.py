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


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [(2, _v2), (3, _v3), (4, _v4)]
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
