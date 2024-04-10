CREATE SCHEMA IF NOT EXISTS chatgpttg;

UPDATE chatgpttg.user SET current_model = 'gpt-4-turbo' WHERE current_model = 'gpt-4-turbo-preview';
UPDATE chatgpttg.user SET current_model = 'gpt-4-turbo' WHERE current_model = 'gpt-4-vision-preview';
UPDATE chatgpttg.user SET current_model = 'gpt-4-turbo' WHERE current_model = 'gpt-4';
