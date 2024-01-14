CREATE SCHEMA IF NOT EXISTS chatgpttg;

ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS system_prompt_settings text;
ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS system_prompt_settings_enabled BOOLEAN NOT NULL DEFAULT false;
