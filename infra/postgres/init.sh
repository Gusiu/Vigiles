#!/bin/sh
# Entry point for /docker-entrypoint-initdb.d in the postgres image.
# init.sql is mounted outside that directory so it only runs with the
# password variables supplied here.
set -eu
psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname postgres \
  -v registry_app_password="$REGISTRY_DB_PASSWORD" \
  -v importer_password="$IMPORTER_DB_PASSWORD" \
  -v hub_app_password="$HUB_DB_PASSWORD" \
  -f /vigiles/init.sql
