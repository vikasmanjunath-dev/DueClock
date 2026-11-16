#!/usr/bin/env bash
# Run the whole test suite against a throwaway local PostgreSQL (needs initdb/pg_ctl on PATH, e.g. brew install postgresql@16, and psycopg2).
# Every tenant database is created fresh and dropped afterwards; nothing outside a temp directory is touched.
set -euo pipefail
PORT="${PGTEST_PORT:-54329}"
DIR="$(mktemp -d)"; SOCK="$(mktemp -d /tmp/dcpg.XXXX)"      # unix socket paths must be short
trap 'pg_ctl -D "$DIR/data" stop -m immediate >/dev/null 2>&1 || true; rm -rf "$DIR" "$SOCK"' EXIT
LC_ALL=C initdb -D "$DIR/data" -U postgres --auth=trust -E UTF8 --locale=C >/dev/null
LC_ALL=C pg_ctl -D "$DIR/data" -o "-p $PORT -k $SOCK -c listen_addresses=127.0.0.1" -l "$DIR/pg.log" -w start >/dev/null
URL="postgresql://postgres@127.0.0.1:$PORT/postgres"
# roles that hosted Postgres (Supabase) has; the lock-down tests need them to exist
psql -q -h 127.0.0.1 -p "$PORT" -U postgres -c "CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN;"
echo "== pass 1: one database per tenant"; DUECLOCK_TEST_PG_URL="$URL" python3 -m pytest -q -p no:cacheprovider "$@"
echo "== pass 2: every tenant in ONE shared schema"; DUECLOCK_TEST_PG_SHARED=1 DUECLOCK_TEST_PG_URL="$URL" python3 -m pytest -q -p no:cacheprovider "$@"
echo "== pass 3: shared schema AND the control plane in PostgreSQL"; DUECLOCK_TEST_PG_SHARED=1 DUECLOCK_TEST_PG_CONTROL=1 DUECLOCK_TEST_PG_URL="$URL" python3 -m pytest -q -p no:cacheprovider "$@"
