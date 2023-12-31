CREATE SCHEMA IF NOT EXISTS chatgpttg;

CREATE TABLE IF NOT EXISTS chatgpttg.tts_usage
(
    id bigserial PRIMARY KEY,
    user_id bigserial NOT NULL,
    model text NOT NULL,
    characters_count bigserial NOT NULL,
    cdate timestamp WITH TIME ZONE NOT NULL default NOW()
);

CREATE INDEX IF NOT EXISTS tts_usage_user_id_idx ON chatgpttg.tts_usage USING hash(user_id);

ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS tts_voice text DEFAULT 'onyx';
