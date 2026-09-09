-- DALL-E 3 image generation was removed; the per-user toggle has no consumer anymore.
-- image_generation_usage stays as billing history.
ALTER TABLE chatgpttg.user DROP COLUMN IF EXISTS image_generation;
