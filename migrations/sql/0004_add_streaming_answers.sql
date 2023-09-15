ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS streaming_answers boolean NOT NULL DEFAULT false;
ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS function_call_verbose boolean NOT NULL DEFAULT false;
