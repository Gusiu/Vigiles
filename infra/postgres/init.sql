-- Databases and roles for Vigiles. Safe to run repeatedly.
--
-- Run with psql variables holding the passwords, for example:
--   psql -v ON_ERROR_STOP=1 \
--        -v registry_app_password=... -v importer_password=... -v hub_app_password=... \
--        -f infra/postgres/init.sql
--
-- registry_app owns the registry database and runs its migrations.
-- importer may only touch the tables it loads; the migrations grant that.
-- hub_app owns the hub database.

SELECT format('CREATE ROLE %I LOGIN', r)
FROM unnest(ARRAY['registry_app', 'importer', 'hub_app']) AS r
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r)
\gexec

ALTER ROLE registry_app WITH LOGIN PASSWORD :'registry_app_password';
ALTER ROLE importer WITH LOGIN PASSWORD :'importer_password';
ALTER ROLE hub_app WITH LOGIN PASSWORD :'hub_app_password';

SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8'' TEMPLATE template0', d, o)
FROM (VALUES ('registry', 'registry_app'), ('hub', 'hub_app')) AS t(d, o)
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = d)
\gexec

REVOKE CONNECT, TEMPORARY ON DATABASE registry FROM PUBLIC;
REVOKE CONNECT, TEMPORARY ON DATABASE hub FROM PUBLIC;
GRANT CONNECT ON DATABASE registry TO registry_app, importer;
GRANT CONNECT ON DATABASE hub TO hub_app;
