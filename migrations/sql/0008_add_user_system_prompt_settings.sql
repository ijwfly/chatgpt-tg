CREATE SCHEMA IF NOT EXISTS chatgpttg;

ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS system_prompt_settings text;
