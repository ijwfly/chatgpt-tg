CREATE SCHEMA IF NOT EXISTS chatgpttg;

ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS image_generation boolean DEFAULT false;
