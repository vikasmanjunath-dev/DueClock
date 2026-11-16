#!/usr/bin/env bash
# Boot the server exactly as the container does (clean virtualenv with ONLY requirements-server.txt, DUECLOCK_ENV=production, real PostgreSQL),
# then exercise it over HTTP. Needs initdb/pg_ctl on PATH and network for pip. Everything lives in a temp directory.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; DIR="$(mktemp -d)"; SOCK="$(mktemp -d /tmp/dcsm.XXXX)"; PGP=54331; APP=18100
trap 'kill "${SRV:-0}" 2>/dev/null || true; pg_ctl -D "$DIR/data" stop -m immediate >/dev/null 2>&1 || true; rm -rf "$DIR" "$SOCK"' EXIT
LC_ALL=C initdb -D "$DIR/data" -U postgres --auth=trust -E UTF8 --locale=C >/dev/null
LC_ALL=C pg_ctl -D "$DIR/data" -o "-p $PGP -k $SOCK -c listen_addresses=127.0.0.1" -l "$DIR/pg.log" -w start >/dev/null
python3 -m venv "$DIR/venv"; "$DIR/venv/bin/pip" install -q -r "$ROOT/requirements-server.txt"
URL="postgresql://postgres@127.0.0.1:$PGP/postgres"
export DUECLOCK_ENV=production DUECLOCK_PUBLIC_URL=https://dueclock.example.test DUECLOCK_TOKEN_SECRET="$(openssl rand -hex 32)" PGPASSWORD=unused \
  DUECLOCK_CONTROL_DB_URL="$URL?schema=dueclock_control&sslmode=disable" DUECLOCK_TENANT_DB_URL="$URL?schema=dueclock&sslmode=disable" \
  DUECLOCK_TRUST_PROXY=1 DUECLOCK_SCHEDULER=1 PORT=$APP DUECLOCK_DATA_DIR="$DIR/appdata"
cd "$ROOT"
echo "== preflight (with database probe)"; "$DIR/venv/bin/python" -m dueclock.core.cli preflight --probe || true
echo "== boot refuses an unsafe configuration"
if env -u DUECLOCK_TOKEN_SECRET "$DIR/venv/bin/python" -m dueclock.core.cli serve --host 127.0.0.1 >"$DIR/refuse.log" 2>&1; then echo "FAIL: started without a token secret"; exit 1; fi
grep -q TOKEN_SECRET_MISSING "$DIR/refuse.log" && echo "ok: refused (TOKEN_SECRET_MISSING)"
echo "== boot"
"$DIR/venv/bin/python" -m dueclock.core.cli serve --host 127.0.0.1 >"$DIR/server.log" 2>&1 & SRV=$!
for i in $(seq 1 40); do curl -sf "http://127.0.0.1:$APP/v1/ready" >/dev/null 2>&1 && break; sleep 0.5; done
curl -sf "http://127.0.0.1:$APP/v1/health"; echo; curl -sf "http://127.0.0.1:$APP/v1/ready"; echo
KEY="$("$DIR/venv/bin/python" -m dueclock.core.cli create-tenant "Smoke Co" | sed -n 's/^API key.*: //p')"
H=(-H "X-API-Key: $KEY" -H "Content-Type: application/json")
echo "== sync + read back"
curl -sf -X POST "${H[@]}" "http://127.0.0.1:$APP/v1/connector/sync" -d '{"connector_version":"0.1.0","records":[{"guid":"s1","invoice_number":"SMOKE/1","invoice_date":"2026-10-01","buyer_name":"Smoke Buyer","amount":1000,"pending_amount":1000}]}'; echo
curl -sf "${H[@]}" "http://127.0.0.1:$APP/v1/invoices" | "$DIR/venv/bin/python" -c "import sys,json; d=json.load(sys.stdin); assert d['count']==1 and d['invoices'][0]['invoice_number']=='SMOKE/1'; print('ok: invoice stored and read back')"
psql -h 127.0.0.1 -p $PGP -U postgres -tAc "select 'tenant rows in dueclock.invoices: '||count(*) from dueclock.invoices"
echo "SMOKE OK"
