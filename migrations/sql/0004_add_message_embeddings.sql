CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE IF EXISTS chatgpttg.message ADD COLUMN embedding vector(1536);
