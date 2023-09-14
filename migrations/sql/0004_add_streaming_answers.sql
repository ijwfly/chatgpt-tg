ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS streaming_answers boolean NOT NULL DEFAULT false;
