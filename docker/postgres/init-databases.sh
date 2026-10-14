#!/bin/bash
# Creates the zammad and copilot databases, each owned by its own
# unprivileged login role. Runs once, when the data volume is first created.
set -o errexit -o pipefail

for app in zammad copilot; do
  var="${app^^}_DB_PASSWORD"
  echo "Creating role and database '${app}'"
  psql --variable ON_ERROR_STOP=1 \
       --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB:-${POSTGRES_USER}}" \
       --variable name="${app}" --variable pass="${!var}" <<'EOSQL'
CREATE ROLE :"name" LOGIN PASSWORD :'pass';
CREATE DATABASE :"name" OWNER :"name";
EOSQL
done
