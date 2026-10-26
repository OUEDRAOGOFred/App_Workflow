#!/bin/sh
set -eu
# psql variables quote password values inside SQL; never interpolate SQL in shell.
psql --no-psqlrc --set=ON_ERROR_STOP=1 \
  --set=agent_password="$AGENT_DB_PASSWORD" \
  --set=workflow_password="$WORKFLOW_DB_PASSWORD" \
  --set=integration_password="$INTEGRATION_DB_PASSWORD" \
  --set=execution_password="$EXECUTION_DB_PASSWORD" \
  --file=/provision/provision.sql
