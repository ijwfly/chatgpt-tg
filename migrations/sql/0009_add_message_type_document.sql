CREATE SCHEMA IF NOT EXISTS chatgpttg;

ALTER TYPE chatgpttg.message_types ADD VALUE IF NOT EXISTS 'document';
