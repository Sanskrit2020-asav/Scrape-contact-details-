-- North Nepal Social Engagement Agent — initial schema.
--
-- Duplicate protection is enforced by the database, not only by application
-- code: (platform, platform_comment_id) is UNIQUE on social_comments and one
-- posted reply per comment is guaranteed by a partial unique index on
-- ai_replies. A retry therefore cannot produce a second Facebook/Instagram
-- reply even if the application layer is buggy or a process is killed midway.

CREATE TABLE IF NOT EXISTS social_posts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    platform          TEXT    NOT NULL,
    platform_post_id  TEXT    NOT NULL,
    account_id        TEXT    NOT NULL DEFAULT '',
    url               TEXT    NOT NULL DEFAULT '',
    caption           TEXT    NOT NULL DEFAULT '',
    content_type      TEXT    NOT NULL DEFAULT 'post',
    topic             TEXT    NOT NULL DEFAULT '',
    posted_at         TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, platform_post_id)
);

CREATE TABLE IF NOT EXISTS social_comments (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    platform             TEXT    NOT NULL,
    platform_comment_id  TEXT    NOT NULL,
    post_id              INTEGER REFERENCES social_posts(id) ON DELETE SET NULL,
    platform_post_id     TEXT    NOT NULL DEFAULT '',
    parent_comment_id    TEXT    NOT NULL DEFAULT '',
    author_id            TEXT    NOT NULL DEFAULT '',
    author_name          TEXT    NOT NULL DEFAULT '',
    comment_text         TEXT    NOT NULL DEFAULT '',
    comment_url          TEXT    NOT NULL DEFAULT '',
    comment_created_at   TEXT,
    intent               TEXT    NOT NULL DEFAULT '',
    confidence           REAL    NOT NULL DEFAULT 0.0,
    risk_level           TEXT    NOT NULL DEFAULT '',
    action               TEXT    NOT NULL DEFAULT '',
    status               TEXT    NOT NULL DEFAULT 'new',
    reason               TEXT    NOT NULL DEFAULT '',
    request_id           TEXT    NOT NULL DEFAULT '',
    processed_at         TEXT,
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, platform_comment_id)
);

CREATE INDEX IF NOT EXISTS idx_comments_status   ON social_comments(status);
CREATE INDEX IF NOT EXISTS idx_comments_platform ON social_comments(platform);
CREATE INDEX IF NOT EXISTS idx_comments_created  ON social_comments(created_at DESC);

CREATE TABLE IF NOT EXISTS ai_replies (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id         INTEGER NOT NULL REFERENCES social_comments(id) ON DELETE CASCADE,
    reply_text         TEXT    NOT NULL DEFAULT '',
    final_reply_text   TEXT    NOT NULL DEFAULT '',
    model              TEXT    NOT NULL DEFAULT '',
    confidence         REAL    NOT NULL DEFAULT 0.0,
    approved           INTEGER NOT NULL DEFAULT 0,
    approved_by        TEXT    NOT NULL DEFAULT '',
    approved_at        TEXT,
    posted             INTEGER NOT NULL DEFAULT 0,
    posted_at          TEXT,
    platform_reply_id  TEXT,
    auto               INTEGER NOT NULL DEFAULT 0,
    dry_run            INTEGER NOT NULL DEFAULT 1,
    error              TEXT    NOT NULL DEFAULT '',
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_replies_comment ON ai_replies(comment_id);

-- At most one *posted* reply per comment. This is the hard duplicate guard.
CREATE UNIQUE INDEX IF NOT EXISTS idx_replies_one_posted_per_comment
    ON ai_replies(comment_id) WHERE posted = 1;

CREATE TABLE IF NOT EXISTS knowledge_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    content      TEXT    NOT NULL,
    category     TEXT    NOT NULL DEFAULT 'general',
    status       TEXT    NOT NULL DEFAULT 'active',
    valid_from   TEXT,
    valid_until  TEXT,
    human_only   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge_items(status);

CREATE TABLE IF NOT EXISTS agent_settings (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    dry_run                  INTEGER NOT NULL DEFAULT 1,
    human_approval_required  INTEGER NOT NULL DEFAULT 1,
    auto_reply_enabled       INTEGER NOT NULL DEFAULT 0,
    minimum_confidence       REAL    NOT NULL DEFAULT 0.90,
    max_reply_length         INTEGER NOT NULL DEFAULT 240,
    polling_interval_seconds INTEGER NOT NULL DEFAULT 300,
    max_comments_per_run     INTEGER NOT NULL DEFAULT 50,
    max_posts_per_run        INTEGER NOT NULL DEFAULT 10,
    openai_model             TEXT    NOT NULL DEFAULT 'gpt-5',
    facebook_enabled         INTEGER NOT NULL DEFAULT 1,
    instagram_enabled        INTEGER NOT NULL DEFAULT 1,
    facebook_comments_actor  TEXT    NOT NULL DEFAULT '',
    facebook_posts_actor     TEXT    NOT NULL DEFAULT '',
    facebook_reply_actor     TEXT    NOT NULL DEFAULT '',
    instagram_comments_actor TEXT    NOT NULL DEFAULT '',
    instagram_posts_actor    TEXT    NOT NULL DEFAULT '',
    instagram_reply_actor    TEXT    NOT NULL DEFAULT '',
    created_at               TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    level       TEXT    NOT NULL DEFAULT 'info',
    platform    TEXT    NOT NULL DEFAULT '',
    comment_id  INTEGER,
    request_id  TEXT    NOT NULL DEFAULT '',
    details     TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_event   ON audit_logs(event_type);
