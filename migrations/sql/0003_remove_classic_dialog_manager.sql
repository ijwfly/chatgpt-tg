DROP TABLE IF EXISTS chatgpttg.dialog;
ALTER TABLE chatgpttg.message DROP COLUMN IF EXISTS dialog_id;
ALTER TABLE chatgpttg.message DROP COLUMN IF EXISTS is_subdialog;
ALTER TABLE chatgpttg.user DROP COLUMN IF EXISTS dynamic_dialog;
