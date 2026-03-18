ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS agent_mode BOOLEAN DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS chatgpttg.plan (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    title TEXT NOT NULL,
    steps JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_chat_id_status ON chatgpttg.plan(chat_id, status);
