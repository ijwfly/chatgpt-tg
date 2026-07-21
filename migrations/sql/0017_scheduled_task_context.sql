ALTER TABLE chatgpttg.scheduled_task
    ADD COLUMN IF NOT EXISTS context_message_ids BIGINT[] NOT NULL DEFAULT '{}';
