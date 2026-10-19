-- Runs once, when the pgdata volume is first initialised (as the superuser).
-- Creates the two databases, the login roles and the extensions.
-- Dev passwords only; they match .env.example. Change them for any shared host.

-- ------------------------------------------------------------------ roles
CREATE ROLE aap_owner LOGIN PASSWORD 'aap_owner';   -- runs Alembic, owns aap tables
CREATE ROLE aap_app   LOGIN PASSWORD 'aap_app';     -- api + worker
CREATE ROLE ebs_owner LOGIN PASSWORD 'ebs_owner';   -- mock EBS schema owner (seeder, import program)
CREATE ROLE apps_ro   LOGIN PASSWORD 'apps_ro';     -- adapter: read-only on apps.*
CREATE ROLE apps_intf LOGIN PASSWORD 'apps_intf';   -- adapter: interface inserts + APXIIMPT submit

-- -------------------------------------------------------------- databases
CREATE DATABASE aap OWNER aap_owner;
CREATE DATABASE ebs OWNER ebs_owner;

REVOKE ALL ON DATABASE ebs FROM PUBLIC;
GRANT CONNECT ON DATABASE ebs TO ebs_owner, apps_ro, apps_intf;
REVOKE ALL ON DATABASE aap FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE aap TO aap_owner, aap_app;

-- Adapter roles only ever see the APPS "synonyms" (views).
ALTER ROLE apps_ro   IN DATABASE ebs SET search_path = apps;
ALTER ROLE apps_intf IN DATABASE ebs SET search_path = apps, ap;
ALTER ROLE apps_ro   SET default_transaction_read_only = on;

-- ------------------------------------------------------------ aap database
\connect aap
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
ALTER SCHEMA public OWNER TO aap_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO aap_app;

-- Everything aap_owner creates later (Alembic, Procrastinate schema) is usable by aap_app.
-- audit_event is narrowed to SELECT/INSERT by its migration (R-05, ARCHITECTURE 8.2).
ALTER DEFAULT PRIVILEGES FOR ROLE aap_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aap_app;
ALTER DEFAULT PRIVILEGES FOR ROLE aap_owner IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO aap_app;
ALTER DEFAULT PRIVILEGES FOR ROLE aap_owner IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO aap_app;
ALTER DEFAULT PRIVILEGES FOR ROLE aap_owner IN SCHEMA public
    GRANT USAGE ON TYPES TO aap_app;

-- ------------------------------------------------------------ ebs database
\connect ebs
ALTER SCHEMA public OWNER TO ebs_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
