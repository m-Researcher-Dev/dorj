-- =========================================================================
-- Dorj — schema v1
-- =========================================================================


-- 1. sites — reference table for the three supported sites
-- -------------------------------------------------------------------------

CREATE TABLE sites (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL UNIQUE,
    home TEXT    NOT NULL
);

INSERT INTO sites (name, home) VALUES
    ('soft98',      'https://soft98.ir'),
    ('p30download', 'https://p30download.ir'),
    ('yasdl',       'https://www.yasdl.com');


-- 2. systems — every Windows branch we support
-- -------------------------------------------------------------------------

CREATE TABLE systems (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT    NOT NULL UNIQUE,
    os         TEXT    NOT NULL CHECK (os IN ('windows')),
    os_version TEXT    NOT NULL,
    arch       TEXT    NOT NULL CHECK (arch IN ('x86', 'x64', 'arm64'))
);

INSERT INTO systems (key, os, os_version, arch) VALUES
    ('windows|7|x86',    'windows', '7',    'x86'),
    ('windows|7|x64',    'windows', '7',    'x64'),
    ('windows|8|x86',    'windows', '8',    'x86'),
    ('windows|8|x64',    'windows', '8',    'x64'),
    ('windows|8.1|x86',  'windows', '8.1',  'x86'),
    ('windows|8.1|x64',  'windows', '8.1',  'x64'),
    ('windows|10|x86',   'windows', '10',   'x86'),
    ('windows|10|x64',   'windows', '10',   'x64'),
    ('windows|10|arm64', 'windows', '10',   'arm64'),
    ('windows|11|x86',   'windows', '11',   'x86'),
    ('windows|11|x64',   'windows', '11',   'x64'),
    ('windows|11|arm64', 'windows', '11',   'arm64');


-- 3. queries — unique normalized search queries
-- -------------------------------------------------------------------------

CREATE TABLE queries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_text TEXT    NOT NULL UNIQUE,
    raw_text        TEXT    NOT NULL,
    created_at      TEXT    NOT NULL
);


-- 4. links — product pages and download links
-- -------------------------------------------------------------------------

CREATE TABLE links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT    NOT NULL UNIQUE,
    type       TEXT    NOT NULL CHECK (type IN ('product', 'download')),
    site_id    INTEGER NOT NULL REFERENCES sites(id),
    parent_id  INTEGER REFERENCES links(id) ON DELETE CASCADE,
    filename   TEXT,
    title      TEXT,
    text       TEXT,
    section    TEXT,
    first_seen TEXT    NOT NULL
);

CREATE INDEX idx_links_site_type ON links(site_id, type);
CREATE INDEX idx_links_parent    ON links(parent_id);


-- 5. solutions — cached answer per (query, system, pipeline version)
-- -------------------------------------------------------------------------

CREATE TABLE solutions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id         INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    system_id        INTEGER NOT NULL REFERENCES systems(id),
    pipeline_version TEXT    NOT NULL,
    status           TEXT    NOT NULL CHECK (status IN ('ok', 'unsupported')),
    quality_score    REAL    NOT NULL DEFAULT 0.0,
    confidence       REAL    NOT NULL DEFAULT 1.0
                             CHECK (confidence >= 0.0 AND confidence <= 1.0),
    created_at       TEXT    NOT NULL,
    last_verified    TEXT,
    blocked_until    TEXT,
    UNIQUE (query_id, system_id, pipeline_version)
);

CREATE INDEX idx_solutions_blocked
    ON solutions(blocked_until) WHERE blocked_until IS NOT NULL;


-- 6. solution_links — many-to-many between solutions and download links
-- -------------------------------------------------------------------------

CREATE TABLE solution_links (
    solution_id INTEGER NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    link_id     INTEGER NOT NULL REFERENCES links(id)     ON DELETE CASCADE,
    rank        INTEGER NOT NULL,
    PRIMARY KEY (solution_id, link_id),
    UNIQUE (solution_id, rank)
);

CREATE INDEX idx_solution_links_link ON solution_links(link_id);


-- 7. events — append-only tracking log
-- -------------------------------------------------------------------------

CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT    NOT NULL,
    user_id     TEXT,
    event_type  TEXT    NOT NULL CHECK (event_type IN (
                    'search', 'extract', 'download', 'install', 'ai'
                )),
    query_id    INTEGER REFERENCES queries(id) ON DELETE SET NULL,
    link_id     INTEGER REFERENCES links(id)   ON DELETE SET NULL,
    system_id   INTEGER REFERENCES systems(id) ON DELETE SET NULL,
    data        TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL
);

CREATE INDEX idx_events_job       ON events(job_id);
CREATE INDEX idx_events_type_time ON events(event_type, created_at);
CREATE INDEX idx_events_link      ON events(link_id);
CREATE INDEX idx_events_user      ON events(user_id);


-- 8. user_installs — per-user installation history
-- -------------------------------------------------------------------------

CREATE TABLE user_installs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT    NOT NULL,
    query_id       INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    link_id        INTEGER REFERENCES links(id) ON DELETE SET NULL,
    system_id      INTEGER NOT NULL REFERENCES systems(id),
    app_name       TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('success', 'failed')),
    install_method TEXT    NOT NULL CHECK (install_method IN ('silent', 'gui', 'ai')),
    version        TEXT,
    installed_at   TEXT    NOT NULL
);

CREATE INDEX idx_user_installs_user  ON user_installs(user_id, installed_at);
CREATE INDEX idx_user_installs_query ON user_installs(query_id);
