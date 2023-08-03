CREATE SCHEMA IF NOT EXISTS chatgpttg;

CREATE TABLE IF NOT EXISTS chatgpttg.user
(
    id bigserial PRIMARY KEY,
    telegram_id bigserial NOT NULL,
    current_model text NOT NULL DEFAULT 'gpt-3.5-turbo',
    gpt_mode text NOT NULL DEFAULT 'assistant',
    forward_as_prompt boolean NOT NULL DEFAULT false,
    voice_as_prompt boolean NOT NULL DEFAULT true,
    use_functions boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS user_telegram_id_idx ON chatgpttg.user USING hash(telegram_id);

CREATE TABLE IF NOT EXISTS chatgpttg.message
(
    id bigserial PRIMARY KEY,
    user_id bigint NOT NULL,
    message jsonb NOT NULL,
    cdate timestamp WITH TIME ZONE NOT NULL default NOW(),
    previous_message_ids BIGINT[] DEFAULT '{}',
    tg_chat_id bigint NOT NULL,
    tg_message_id bigint NOT NULL
);

CREATE INDEX IF NOT EXISTS message_user_id_idx ON chatgpttg.message USING hash(user_id);
CREATE INDEX IF NOT EXISTS message_cdate_idx ON chatgpttg.message USING btree(cdate);
CREATE INDEX IF NOT EXISTS message_tg_message_id_idx ON chatgpttg.message USING hash(tg_message_id);
