CREATE TABLE IF NOT EXISTS chatgpttg.scheduled_task (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    schedule_type TEXT NOT NULL,
    run_at TIMESTAMP WITH TIME ZONE,
    cron_expression TEXT,
    next_execution TIMESTAMP WITH TIME ZONE,
    last_execution TIMESTAMP WITH TIME ZONE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sched_next_exec
    ON chatgpttg.scheduled_task(next_execution) WHERE enabled = TRUE;
CREATE INDEX IF NOT EXISTS idx_sched_chat_id
    ON chatgpttg.scheduled_task(chat_id, enabled);
