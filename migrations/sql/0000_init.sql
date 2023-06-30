CREATE SCHEMA chatgpttg;

CREATE TABLE chatgpttg.user
(
    id bigserial PRIMARY KEY,
    telegram_id bigserial NOT NULL
);

CREATE INDEX user_telegram_id_idx ON chatgpttg.user USING hash(telegram_id);

CREATE TABLE chatgpttg.dialog
(
    id bigserial PRIMARY KEY,
    user_id bigint NOT NULL,
    chat_id bigint NOT NULL,
    cdate timestamp WITH TIME ZONE NOT NULL default NOW(),
    is_active boolean NOT NULL DEFAULT true,
    model text NOT NULL
);

CREATE INDEX dialog_user_id_idx ON chatgpttg.dialog USING hash(user_id);
CREATE INDEX dialog_chat_id_idx ON chatgpttg.dialog USING hash(chat_id);
CREATE INDEX dialog_cdate_idx ON chatgpttg.dialog USING btree(cdate);

CREATE TABLE chatgpttg.message
(
    id bigserial PRIMARY KEY,
    dialog_id bigint NOT NULL,
    user_id bigint NOT NULL,
    message jsonb NOT NULL,
    cdate timestamp WITH TIME ZONE NOT NULL default NOW(),
    previous_message_ids BIGINT[] DEFAULT '{}',
    is_subdialog boolean NOT NULL DEFAULT false,
    tg_chat_id bigint NOT NULL,
    tg_message_id bigint NOT NULL
);

CREATE INDEX message_dialog_id_idx ON chatgpttg.message USING hash(dialog_id);
CREATE INDEX message_user_id_idx ON chatgpttg.message USING hash(user_id);
CREATE INDEX message_cdate_idx ON chatgpttg.message USING btree(cdate);
