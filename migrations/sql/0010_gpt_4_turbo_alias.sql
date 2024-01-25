CREATE SCHEMA IF NOT EXISTS chatgpttg;

UPDATE chatgpttg.user SET current_model = 'gpt-4-turbo-preview' WHERE current_model = 'gpt-4-1106-preview';
