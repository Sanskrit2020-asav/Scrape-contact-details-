-- Token accounting and a spend cap.
--
-- Every OpenAI call already returns a usage block; before this migration it was
-- read and discarded. Recording it makes spend visible in the dashboard and,
-- more importantly, gives the agent a budget it can enforce on itself: an
-- unattended poller that starts looping on a bad actor response should stop
-- costing money, not keep going until someone notices the bill.

CREATE TABLE IF NOT EXISTS ai_usage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id     INTEGER REFERENCES social_comments(id) ON DELETE SET NULL,
    platform       TEXT    NOT NULL DEFAULT '',
    model          TEXT    NOT NULL DEFAULT '',
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    total_tokens   INTEGER NOT NULL DEFAULT 0,
    attempts       INTEGER NOT NULL DEFAULT 1,
    request_id     TEXT    NOT NULL DEFAULT '',
    response_id    TEXT    NOT NULL DEFAULT '',
    succeeded      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_created ON ai_usage(created_at DESC);

-- 0 disables the cap. Prices are per 1,000,000 tokens, and are configuration
-- rather than constants because published rates change and differ per model --
-- the app must never present a stale rate as fact.
ALTER TABLE agent_settings ADD COLUMN daily_token_budget INTEGER NOT NULL DEFAULT 0;
ALTER TABLE agent_settings ADD COLUMN input_cost_per_million  REAL NOT NULL DEFAULT 0.0;
ALTER TABLE agent_settings ADD COLUMN output_cost_per_million REAL NOT NULL DEFAULT 0.0;
