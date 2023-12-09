CREATE SCHEMA IF NOT EXISTS chatgpttg;

CREATE TABLE IF NOT EXISTS chatgpttg.image_generation_usage
(
    id bigserial PRIMARY KEY,
    user_id bigserial NOT NULL,
    model text NOT NULL,
    resolution text NOT NULL,
    cdate timestamp WITH TIME ZONE NOT NULL default NOW()
);

CREATE INDEX IF NOT EXISTS image_generation_usage_user_id_idx ON chatgpttg.image_generation_usage USING hash(user_id);
