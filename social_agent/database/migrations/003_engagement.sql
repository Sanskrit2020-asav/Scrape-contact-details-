-- Comment density. A post whose comment count reaches this threshold is
-- treated as high-engagement: its comments are processed first and the model is
-- shown a wider slice of approved knowledge when answering them.
ALTER TABLE agent_settings ADD COLUMN high_engagement_threshold INTEGER NOT NULL DEFAULT 10;
