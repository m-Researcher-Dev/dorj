-- =========================================================================
-- Dorj — schema v2: subscriptions
-- =========================================================================


CREATE TABLE plans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    display_name  TEXT    NOT NULL,
    quota_total   INTEGER,
    duration_days INTEGER NOT NULL,
    price_irr     INTEGER,
    is_active     INTEGER NOT NULL DEFAULT 1
                          CHECK (is_active IN (0, 1)),
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL
);


CREATE TABLE subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    plan_id     INTEGER NOT NULL REFERENCES plans(id),
    quota_total INTEGER,
    quota_used  INTEGER NOT NULL DEFAULT 0
                        CHECK (quota_used >= 0),
    started_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'expired', 'cancelled')),
    note        TEXT,
    created_at  TEXT    NOT NULL
);

CREATE INDEX idx_subscriptions_user_active
    ON subscriptions(user_id, status);

CREATE INDEX idx_subscriptions_expires
    ON subscriptions(expires_at) WHERE status = 'active';


INSERT INTO plans 
    (name, display_name, quota_total, duration_days, price_irr, sort_order, created_at)
VALUES 
    ('trial', 'آزمایشی', 3, 7, NULL, 0, datetime('now'));
