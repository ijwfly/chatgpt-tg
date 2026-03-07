CREATE SCHEMA IF NOT EXISTS chatgpttg;

-- Migrate all users from removed models to gpt-4.1
UPDATE chatgpttg.user SET current_model = 'gpt-4.1' WHERE current_model IN (
    'gpt-4o',
    'gpt-4o-mini',
    'gpt-4-turbo',
    'gpt-4-turbo-preview',
    'gpt-4-vision-preview',
    'gpt-4',
    'gpt-3.5-turbo',
    'gpt-3.5-turbo-16k'
);

-- Update default for new users
ALTER TABLE chatgpttg.user ALTER COLUMN current_model SET DEFAULT 'gpt-4.1';
