#!/usr/bin/env bash
# Loads supabase/schema.sql into a scratch database twice (it must stay
# re-runnable), then runs schema_tests.sql, which stops at the first failed
# assertion. Needs a Postgres the current user can create databases on:
#   PGHOST=127.0.0.1 PGPORT=5432 PGUSER=postgres tests/sql/run.sh
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$(dirname "$here")")"
db="pawmodoro_schema_test"
psql_q() { psql -v ON_ERROR_STOP=1 -q "$@"; }
psql_q -d postgres -c "drop database if exists $db" -c "create database $db"
psql_q -d "$db" -f "$here/auth_stub.sql"
# A leftover from an older schema that schema.sql must clean up.
psql_q -d "$db" -c "create function add_task(p_text text, p_recurrence text, p_reminder_time text) returns void language sql as 'select'"
psql_q -d "$db" -f "$root/supabase/schema.sql" > /dev/null
psql_q -d "$db" -f "$root/supabase/schema.sql" > /dev/null
# Query results go nowhere; a failed assertion is an error on stderr.
psql_q -d "$db" -o /dev/null -f "$here/schema_tests.sql"
echo "schema tests passed"
