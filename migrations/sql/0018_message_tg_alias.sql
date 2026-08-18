-- Extra Telegram message ids that resolve to an existing dialog message.
-- Used when several chat messages (e.g. the user's uploaded document and the bot's
-- "Saved to agent workspace" confirmation) should lead to the same dialog branch on reply.
CREATE TABLE IF NOT EXISTS chatgpttg.message_tg_alias
(
    id bigserial PRIMARY KEY,
    message_id bigint NOT NULL REFERENCES chatgpttg.message(id) ON DELETE CASCADE,
    tg_chat_id bigint NOT NULL,
    tg_message_id bigint NOT NULL,
    cdate timestamp WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS message_tg_alias_chat_msg_idx
    ON chatgpttg.message_tg_alias (tg_chat_id, tg_message_id);
