ALTER TABLE chatgpttg.message ADD COLUMN IF NOT EXISTS activation_dtime TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW();

-- remove dialog entity used for old dialog mechanism
DROP TABLE IF EXISTS chatgpttg.dialog;
ALTER TABLE chatgpttg.message DROP COLUMN IF EXISTS dialog_id;
ALTER TABLE chatgpttg.message DROP COLUMN IF EXISTS is_subdialog;
ALTER TABLE chatgpttg.user DROP COLUMN IF EXISTS dynamic_dialog;

-- create message_type field
DO $$
BEGIN
   IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'message_types') THEN
      CREATE TYPE chatgpttg.message_types AS ENUM ('message', 'summary', 'reset');
   END IF;
END
$$;

ALTER TABLE chatgpttg.message ADD COLUMN IF NOT EXISTS message_type chatgpttg.message_types NOT NULL DEFAULT 'message';
