-- ClipFarm local database schema (SQLite, WAL mode).
-- Applied idempotently on startup by clipfarm.db.init_db(); bump schema_version for migrations.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

-- Key/value app settings (non-secret). Secrets live in the OS keychain, never here.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL            -- JSON-encoded
);

CREATE TABLE IF NOT EXISTS games (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    twitch_id     TEXT,              -- Twitch category id
    youtube_query TEXT,              -- search string used for YouTube
    kick_slug     TEXT,
    box_art_url   TEXT,
    trending_rank INTEGER,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name)
);

-- One row per channel per platform.
CREATE TABLE IF NOT EXISTS streamers (
    id                 INTEGER PRIMARY KEY,
    platform           TEXT NOT NULL CHECK (platform IN ('twitch','youtube','kick')),
    platform_user_id   TEXT NOT NULL,
    login              TEXT NOT NULL,
    display_name       TEXT NOT NULL,
    avatar_url         TEXT,
    followers          INTEGER,
    -- Permission gate: only 'allowed' and 'program_member' may be clipped.
    permission_status  TEXT NOT NULL DEFAULT 'unknown'
                       CHECK (permission_status IN ('allowed','program_member','unknown','prohibited')),
    permission_note    TEXT,          -- where the permission came from (URL, Discord msg, etc.)
    best_platforms     TEXT,          -- JSON list e.g. ["youtube_shorts","tiktok"]
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, platform_user_id)
);

-- Time series used for avg viewers / growth trend.
CREATE TABLE IF NOT EXISTS streamer_snapshots (
    id           INTEGER PRIMARY KEY,
    streamer_id  INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
    game_id      INTEGER REFERENCES games(id),
    is_live      INTEGER NOT NULL,
    viewers      INTEGER,
    followers    INTEGER,
    stream_title TEXT,
    taken_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_snap_streamer_time ON streamer_snapshots(streamer_id, taken_at);

-- Clipping rewards programs. User-editable; many programs have no public API.
CREATE TABLE IF NOT EXISTS programs (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,           -- e.g. "xQc Clipping (Whop)"
    operator          TEXT NOT NULL,           -- whop | vyro | clipping_net | discord | other
    url               TEXT,
    payout_per_1k     REAL,                    -- currency units per 1,000 views
    currency          TEXT DEFAULT 'USD',
    min_views         INTEGER,                 -- views required before payout
    max_payout        REAL,
    allowed_platforms TEXT,                    -- JSON list
    required_tags     TEXT,                    -- JSON list of hashtags / mentions
    required_credit   TEXT,                    -- text overlay / description credit template
    disclosure        TEXT,                    -- e.g. "#ad" or "Paid clipping partner"
    rules             TEXT,                    -- free text shown in the rules screen
    source            TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','community','api')),
    verified_at       TEXT,
    active            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS streamer_programs (
    streamer_id INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
    program_id  INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    PRIMARY KEY (streamer_id, program_id)
);

-- Cached ranking output (refreshed on a schedule).
CREATE TABLE IF NOT EXISTS rankings (
    streamer_id        INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
    game_id            INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    profit_score       REAL NOT NULL,
    audience_score     REAL, payout_score REAL, competition_score REAL, highlight_score REAL,
    earnings_tier      TEXT,             -- labelled ESTIMATE in UI
    competition_count  INTEGER,          -- est. number of other clippers covering them
    computed_at        TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (streamer_id, game_id)
);

CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    game_id         INTEGER REFERENCES games(id),
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','running','paused','stopped','completed','error')),
    duration_sec    INTEGER NOT NULL,           -- requested run duration
    started_at      TEXT,
    ends_at         TEXT,
    output_settings TEXT NOT NULL,              -- JSON: platforms, clip length, caption style, schedule
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS project_streamers (
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    streamer_id INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, streamer_id)
);

-- Source media we pull highlights from.
CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY,
    streamer_id   INTEGER NOT NULL REFERENCES streamers(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('featured_clip','community_clip','vod','live')),
    platform_id   TEXT NOT NULL,              -- clip slug / VOD id
    url           TEXT NOT NULL,
    title         TEXT,
    view_count    INTEGER,
    duration_sec  REAL,
    created_at_platform TEXT,
    local_path    TEXT,                       -- NULL once deleted by storage manager
    bytes         INTEGER,
    status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','downloading','ready','processed','deleted','error')),
    UNIQUE (streamer_id, kind, platform_id)
);

-- Candidate moments produced by the highlight detector.
CREATE TABLE IF NOT EXISTS moments (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    start_sec    REAL NOT NULL,
    end_sec      REAL NOT NULL,
    score        REAL NOT NULL,
    signals      TEXT NOT NULL,              -- JSON breakdown {chat:0.8,audio:0.6,...}
    transcript   TEXT,
    fingerprint  TEXT,                        -- for dedupe (streamer + rounded wall-clock time)
    status       TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','queued','rendered','rejected','duplicate')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_moments_fp ON moments(fingerprint);

CREATE TABLE IF NOT EXISTS clips (
    id            INTEGER PRIMARY KEY,
    project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    moment_id     INTEGER REFERENCES moments(id) ON DELETE SET NULL,
    streamer_id   INTEGER REFERENCES streamers(id),
    game_id       INTEGER REFERENCES games(id),
    program_id    INTEGER REFERENCES programs(id),
    file_path     TEXT,
    thumb_path    TEXT,
    duration_sec  REAL,
    hook_text     TEXT,
    title         TEXT,
    description   TEXT,
    hashtags      TEXT,                       -- JSON list
    layout        TEXT,                       -- split | crop | blur_fill
    caption_style TEXT,
    content_hash  TEXT,                       -- sha256 of rendered file, blocks duplicate uploads
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Connected social accounts (tokens in OS keychain under key "account:<id>").
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY,
    platform    TEXT NOT NULL CHECK (platform IN ('youtube','tiktok','instagram','x')),
    handle      TEXT NOT NULL,
    external_id TEXT,
    connected_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, external_id)
);

CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY,
    clip_id       INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    scheduled_at  TEXT NOT NULL,
    posted_at     TEXT,
    external_id   TEXT,
    url           TEXT,
    status        TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','uploading','posted','failed','cancelled')),
    error         TEXT,
    UNIQUE (clip_id, account_id)              -- never upload the same clip twice to one account
);

CREATE TABLE IF NOT EXISTS post_metrics (
    post_id   INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    taken_at  TEXT NOT NULL DEFAULT (datetime('now')),
    views     INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
    PRIMARY KEY (post_id, taken_at)
);

CREATE TABLE IF NOT EXISTS payouts (
    id           INTEGER PRIMARY KEY,
    program_id   INTEGER NOT NULL REFERENCES programs(id),
    post_id      INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    amount       REAL NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'USD',
    status       TEXT NOT NULL DEFAULT 'estimated' CHECK (status IN ('estimated','pending','approved','paid','rejected')),
    submitted_at TEXT, paid_at TEXT, note TEXT
);

-- Durable job queue: survives crashes/reboots; the engine re-claims 'running' jobs on start.
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY,
    project_id   INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,   -- discover | fetch_sources | download | detect | render | post | metrics | cleanup
    payload      TEXT NOT NULL,   -- JSON
    status       TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','cancelled','paused')),
    priority     INTEGER NOT NULL DEFAULT 5,
    progress     REAL NOT NULL DEFAULT 0,
    eta_sec      INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    run_after    TEXT NOT NULL DEFAULT (datetime('now')),
    error        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_jobs_claim ON jobs(status, run_after, priority);

CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    level   TEXT NOT NULL,
    source  TEXT NOT NULL,
    message TEXT NOT NULL,
    job_id  INTEGER
);
