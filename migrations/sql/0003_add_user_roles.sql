ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS full_name text;
ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS username text;

-- create user_role field
DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_roles') THEN
            CREATE TYPE chatgpttg.user_roles AS ENUM ('admin', 'advanced', 'basic', 'stranger');
        END IF;
    END
$$;
ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS role chatgpttg.user_roles;
ALTER TABLE chatgpttg.user ADD COLUMN IF NOT EXISTS cdate timestamp WITH TIME ZONE NOT NULL default NOW();

