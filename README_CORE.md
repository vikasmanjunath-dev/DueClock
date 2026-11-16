# DueClock core

The tested engine behind the product, in `dueclock/core/`. The older prototype (`dueclock/app.py` and friends) stays as a demo; new work goes here.

## Run

```bash
python3 -m pytest -q                                                    # 392 tests on SQLite; ./scripts/test_postgres.sh runs the suite three times on PostgreSQL (428 / 413 / 413)
python3 -m dueclock.core.cli create-tenant "Customer name" --data-dir ./data   # prints the API key once
python3 -m dueclock.core.cli serve --data-dir ./data --port 8100
```

## API (header `X-API-Key`)

| Route | What |
|---|---|
| `GET /v1/health` | status; says whether the bank-rate table is confirmed |
| `PUT /v1/supplier` | seller profile: category, activity, Udyam registration date, UPI VPA |
| `POST /v1/connector/sync` | idempotent sync from the Tally connector; bad records are reported, not fatal |
| `GET /v1/invoices?as_of=&include_paid=` | open invoices with the statutory clock result |
| `GET /v1/invoices/{id}` | one invoice with its clock result |
| `PUT /v1/buyers/{id}/agreement` | seller sets the written-agreement flag (15 vs up to 45 days); audited |
| `GET /v1/syncs` | sync history (connector and Tally version, errors) |
| `GET /v1/connector/ping` | key check for the connector; minimum connector version |
| `POST /v1/invoices/{id}/link` | create the buyer link (signed, expiring; `ttl_days` optional) |
| `GET /v1/invoices/{id}/links`, `DELETE ...` | list link views; revoke every link for the invoice |
| `GET /v1/invoices/{id}/events` | what the buyer did: views, promised dates, disputes |
| `PUT /v1/invoices/{id}/dispute` | seller marks a dispute resolved |

## Modules

- `rates.py`: dated bank-rate table (default is the report's 6.5%, flagged unconfirmed).
- `clock.py`: pure statutory-clock engine: due dates, interest (3x bank rate, monthly rests), coverage, year-end exposure, review flags, rule trace.
- `store.py`: one database per customer (SQLite now, PostgreSQL URL supported), hashed API keys, tenant-scoped repository, audit log.
- `ingest.py`: validation and idempotent upsert of connector records.
- `tally_parse.py`: Tally XML and export parser (fixes the prototype's leaf-element bug).
- `cadence_plan.py`: reminder planner (5 steps, opt-in, quiet hours, daily cap, promise date, stop conditions).
- `api.py`, `cli.py`, `main.py`: FastAPI router, command line, ASGI entry.

## Open items (flagged in the engine, not silently decided)

- Bank-rate history: replace the default with the RBI-notified table, then mark entries confirmed.
- Acceptance date vs delivery date (`ACCEPTANCE_DATE_IGNORED`) and Udyam registration date vs supply date (`REGISTERED_AFTER_SUPPLY`): need a domain reviewer's ruling.
- Rate policy while overdue: `AT_DUE_DATE` (default) or `THROUGH_PERIOD`.
- Expected values in `tests/core/test_clock.py` are computed from the rules and await a reviewer's sign-off.

## Connector (`dueclock/connector/`)

```bash
cp connector.example.json connector.json        # cloud_url, api_key (or env DUECLOCK_API_KEY), tally_port, companies
python3 -m dueclock.connector probe    --config connector.json   # is Tally reachable; TallyPrime or ERP 9
python3 -m dueclock.connector diagnose --config connector.json   # Tally, companies, cloud key, queue, last runs
python3 -m dueclock.connector run-once --config connector.json [--dry-run] [--confirm-vanished]
python3 -m dueclock.connector import-export bills.csv --config connector.json   # fallback when live sync fails
python3 -m dueclock.connector serve    --config connector.json   # nightly at schedule_hour, retries the queue
```

- **Read-only by construction:** every request passes `assert_read_only`; Import, Alter, Delete and ACTION attributes are refused before any socket opens.
- **Durable queue:** SQLite; survives restarts; exponential backoff with jitter; dead-letter after `max_attempts`; 401 stops uploads without losing data; 413 splits the batch.
- **Only changes are sent:** per-bill hashes. Bills that vanish from Tally are reported as settled, unless too many vanish at once (default more than 50% and at least 5): then the run is `held` until `--confirm-vanished`.
- **Two Tally response shapes are parsed:** nested bill nodes and the flat 'Bills Receivable' report. The flat shape has no original amount, so the cloud keeps the first-seen amount (`amount_known=false`).
- **Not verified against a real TallyPrime yet:** the request XML (`Bills Receivable`, `List of Companies`) and the flat-report tags come from my knowledge of Tally's interface and are covered only by a fake server. The first real-install test must confirm them.

## Free exposure scan (`/exposure-scan`, no login)

A buyer or CA pastes a payables list (CSV with a header row, or a Tally Bills Payable XML) and sees, for the financial year end, the bills that could lose the 43B(h) deduction.

- **Honest totals:** *confirmed* (vendors known to be Micro or Small manufacturers or service providers, from the Category and Activity columns) is shown apart from *possible* (vendors whose status is unknown, assuming 15 days; an upper bound). Traders and medium or large vendors are excluded. Written agreements come from a Credit Days column (at most 45) or the page's default terms.
- **Per bill:** statutory due date, days left or overdue, status (past the limit now, will pass the limit before year end, within the limit, not covered, unknown, settled), amount at risk, MSMED interest; a 'Pay these first' list.
- **Gate:** the headline numbers are free; vendors, bills and the CSV unlock with a valid email and a consent tick (stored as a lead). CSV cells are protected against spreadsheet formula injection.
- **Privacy:** the pasted list is kept 24 hours under an unguessable link (192-bit), purged on expiry, deletable at once; no raw IP is stored (a salted hash for the 10-scans-per-hour limit); 1.2 MB and 5,000-bill caps.
- **API:** `POST /v1/scan` (API key) takes `csv`, `tally_xml` or `rows` and returns the full JSON. `status_lookup` in `scan.run_scan` is the hook for a Udyam verification API (manual for pilots, D-22).
- Same engine and rules as the seller side; the reviewer-pending 43B(h) payment-basis rule applies (a bill still unpaid at year end after its limit is treated as exposed).

## Owner screen (`/app`)

Open `/app/login` and enter the company access key. Server-rendered pages, no JavaScript, phone friendly.

- **Collections:** outstanding, overdue, due in 7 days, interest accruing, 43B(h) exposure; every open invoice with flags (Disputed, Promise date, Paused, No consent, Not covered, 43B(h) exposure, part paid awaiting Tally) and the next reminder step; filters; warnings for no or stale Tally sync, paused reminders and unmatched bank credits.
- **Invoice:** the clock working ("how this was worked out"), review flags, written-agreement form (agreed days up to 45), consent (a tick box confirming the buyer agreed; withdrawal stops reminders), pause or resume, create or revoke the buyer link and see the URL and views, resolve a dispute, and the full history.
- **Payments:** paste a bank CSV, review suggested matches with their confidence and reasons, confirm, reject, undo, assign by hand, ignore.
- **Settings:** pause or resume all reminders, WhatsApp sender status, what would be sent now, recent Tally syncs.
- **Security:** signed HttpOnly SameSite=Strict session cookie (12 h), CSRF token on every action, login rate limit (10 failures per 15 minutes per address), strict CSP with no scripts, `no-store`, everything escaped, each session sees only its own company.

## Payment matching

API: `POST /v1/credits/import` (CSV text or rows), `GET /v1/credits[?status=]`, `GET /v1/credits/{id}`, `POST /v1/credits/{id}/suggest|manual|ignore`, `POST /v1/matches/{id}/confirm|reject|unconfirm`, `GET /v1/reconciliation/summary`.

- **Import:** bank CSV (header found below any preamble; Date, Narration, Deposit or Credit, Reference columns; Indian amounts and date formats) or JSON rows. Debits are ignored. Repeat imports skip duplicates; two identical rows in one file stay two credits.
- **Narration parsing:** UPI/IMPS 12-digit reference, NEFT/RTGS bank reference, payer VPA, payer name.
- **Rules, scored 0 to 1:** invoice number in the narration (+0.50); amount equals an invoice or a subset of one buyer's invoices (+0.35; lump sums solved as a subset-sum); short by up to 2.5% for TDS (+0.25); payer name resembles the buyer or a learned alias (up to +0.30); credit within 60 days of the invoice (+0.05). Credits before the invoice date or older than a year never match. A unique exact amount with no other evidence is a weak suggestion.
- **Every suggestion needs a person to confirm** (`auto_confirm_at` is off by default; when set, only a single suggestion at or above that score is confirmed automatically).
- **A confirmed match stops reminders at once**: it records the money as `unposted` on the invoice. Effective outstanding = Tally outstanding minus unposted, used by the clock, buyer page and reminder engine. When Tally later posts the payment, the next sync reduces `unposted` by the same amount, so nothing is counted twice. A confirmed match can be undone; a thank-you (S7) goes out if we had reminded the buyer.
- Confirming a match teaches the payer name for next time.

## Reminder sending

```bash
export DUECLOCK_WA_MODE=cloud DUECLOCK_WA_PHONE_NUMBER_ID=... DUECLOCK_WA_TOKEN=... DUECLOCK_WA_APP_SECRET=... DUECLOCK_WA_VERIFY_TOKEN=...
python3 -m dueclock.core.cli send-reminders --data-dir ./data --dry-run    # decisions only
python3 -m dueclock.core.cli send-reminders --data-dir ./data              # run from cron every 30 minutes
```

API: `PUT/DELETE /v1/buyers/{id}/opt-in`, `PUT /v1/buyers/{id}/language`, `POST /v1/invoices/{id}/pause|resume`, `POST /v1/reminders/pause|resume|run`, `GET /v1/reminders/preview|stats`, `GET|POST /v1/webhooks/whatsapp`.

- **Engine (`reminders.py`)** turns planner decisions into sent templates. One database row per (invoice, step) is claimed *before* the provider is called, so a repeat run, a crash or two workers cannot send twice. An unknown outcome (exception after send) is marked `unknown` and never resent automatically.
- **Rules enforced:** opted-in buyers only; never paid, disputed, paused or opted-out; 09:00 to 19:00 IST only; one message per buyer per day; if steps were missed only the latest is sent; the buyer page link is created or reused for the Pay button.
- **Failures:** transient errors retry with backoff (5, 15, 60, 240 minutes, max 5 attempts); permanent errors are `failed`; an auth fault stops the run; 3 consecutive failures open a circuit breaker; a per-run cap (200) limits damage from a bug.
- **Extra messages:** thank-you when a buyer promises a date (S6), payment received (S7, only if we had reminded), and a follow-up on the promised date (P1).
- **Delivery webhook:** signature-checked (HMAC) status updates move `sent`, `delivered`, `read` forward only; `failed` is recorded; `GET /v1/reminders/stats` gives the open rate.
- **Senders:** `CloudApiSender` (Meta Cloud API or a BSP with the same payloads) and `MockSender`. Error-code mapping is from my recollection of Meta's documentation [A]; check it before the first live send. Template names and variable order match `blueprint/15_WhatsApp_Template_Tracker.xlsx`.
- English templates only until Hindi and Kannada are approved (`approved_languages`).

## Buyer page (`/p/{token}`, no login)

- Server-rendered HTML, about 4 KB (budget 50 KB), no JavaScript, no images, no external requests. Works on 2G and without scripts.
- Shows the supplier, invoice, amount due, due date and days left or overdue; a UPI deep link to the **seller's own** VPA (we never hold money); a promise-a-date form (tomorrow to 60 days); a dispute form; a link to the free scan; privacy and grievance footer.
- The tax note ('may not be deductible in FY..., check with your CA, not legal advice') appears only for covered suppliers with a complete profile. Interest is hidden unless `show_interest=True`.
- Links are HMAC-signed with an expiry and checked against a link record (revocable, bound to one invoice and one customer). Invalid, expired, revoked and tampered links all return the same generic 404.
- Strict headers: CSP (style by hash, `default-src 'none'`), `no-store`, `noindex`, `no-referrer`, `nosniff`, `DENY` framing. Five promise or dispute submissions per link per hour.
- A promised date feeds the reminder planner (`PROMISE_PENDING`); a dispute stops reminders and survives Tally syncs until the seller resolves it.
- Config on `create_core_app`: `public_base_url`, `scan_url`, `privacy_url`, `grievance_email`, `link_ttl_days`, `show_interest`; token secret from `DUECLOCK_TOKEN_SECRET` or a generated `token_secret` file.

## PostgreSQL path

Each customer can have its own PostgreSQL database: `python3 -m dueclock.core.cli create-tenant "Name" --db-url postgresql://user@host/dbname` (keep passwords out of the URL: use `PGPASSWORD` or `.pgpass`; the URL is stored in the control database, which itself stays SQLite). The whole suite also runs on PostgreSQL 16 with psycopg2: `./scripts/test_postgres.sh` starts a throwaway local cluster and runs every test with each tenant in a fresh database (or set `DUECLOCK_TEST_PG_URL` yourself; see `tests/conftest.py`). PostgreSQL-only checks are in `tests/core/test_pg_path.py` (14 tests: 64-bit money columns, plain-number aggregates, `%` in SQL, unique violations inside transactions, 8 concurrent workers claiming one reminder step, idempotent and upgrading migrations, database-per-tenant isolation).
Found and fixed by this test: PostgreSQL `INTEGER` is 32 bits, so paise overflowed above about Rs 2.1 crore (columns are now `BIGINT`/`DOUBLE PRECISION` on PostgreSQL); literal `%` in SQL broke psycopg2; `SUM()` returned `Decimal`; a duplicate insert inside a transaction aborted it (now a savepoint); and the **reminder idempotency index did not exist on either database** (`UNIQUE (invoice_id, step)` is now created, so two workers can no longer both send the same step). Existing databases created before this change are upgraded on open, but index creation fails if they already hold duplicate (invoice, step) rows: delete the extra rows first.
Not covered: connection pooling (one connection per store, fine for pilots), load, `pg_dump` backup/restore drills, and a PostgreSQL control plane.

### Shared database (all customers in one PostgreSQL, e.g. Supabase)

Every tenant table already carries `tenant_id` and every query is filtered by it, so several customers can share one database: give them the same URL with a dedicated schema, `postgresql://USER@HOST:5432/postgres?schema=dueclock&sslmode=require` (optionally `export DUECLOCK_TENANT_DB_URL=...` so `create-tenant "Name"` uses it by default). `./scripts/test_postgres.sh` pass 2 runs the **whole suite with every test tenant in one schema**, and `tests/core/test_shared_db.py` creates two tenants with IDENTICAL data (same invoice numbers, buyers, bank rows) and checks nothing leaks through listings, ids, links, reminders, payments or sync history.
- **Lock-down (Supabase exposes `public` to a public REST key):** use a non-public schema (`?schema=`). On open, every table gets row-level security enabled with no policies and `anon`/`authenticated` lose all privileges on our schema. `python3 -m dueclock.core.cli db-check --db-url ...` reports tables without RLS and any role that can read them (exit 1 if so).
- Use the **direct or session-pooler connection (port 5432)**, not the transaction pooler (6543): the schema is selected per session. Supabase's direct host is IPv6-only unless the IPv4 add-on is on; the session pooler (`aws-0-<region>.pooler.supabase.com`, user `postgres.<project-ref>`) works over IPv4.
- Keep the password out of the URL: set `PGPASSWORD` (or `.pgpass`). The URL is stored in the control database.
- **Control plane in PostgreSQL too:** set `DUECLOCK_CONTROL_DB_URL=postgresql://USER@HOST:5432/postgres?schema=dueclock_control` (a different schema from the tenants; the URL **must** carry `?schema=`, the public schema is refused). It holds API-key hashes, the tenant list, the WhatsApp message index and exposure-scan leads (email addresses), so nothing that matters lives on the app server's disk and several servers can share it (a key created on one works on another at once; deactivating revokes everywhere). It gets the same row-level-security lock-down. Check it with `DUECLOCK_CONTROL_DB_URL=... python3 -m dueclock.core.cli db-check --control`. Existing local `control.db`: `DUECLOCK_CONTROL_DB_URL=... python3 -m dueclock.core.cli migrate-control --data-dir ./data` copies tenants, message index, scans and leads (repeatable; existing customers' API keys keep working).
- **No accidental local customer data:** with the control database in PostgreSQL, `create-tenant` refuses to run unless a customer database is given (`DUECLOCK_TENANT_DB_URL` or `--db-url`); pass `--db-url sqlite:///path` explicitly if a local file is really intended. (Found when a customer was created in a shell that lacked the variable and silently stored its invoices in a local file.)
- **API keys without leaking them:** `create-tenant "Name" --copy-key` and `rotate-key <tenant id> --copy-key` put the key on the clipboard and never print it (keeps it out of screenshots and terminal scrollback; falls back to printing if no clipboard is found). `deactivate-tenant <tenant id>` retires a customer's key at once and keeps its data; `list-tenants` shows ids and active state. A key is stored only as a hash and cannot be shown again: lose it and you rotate.
- **Still on local disk:** the buyer-link/session signing secret (`token_secret` in the data directory). On a server with non-persistent disk set `DUECLOCK_TOKEN_SECRET` (a long random string, same on every server) or every issued buyer link and owner session stops working after a redeploy.
- The control database also stores each customer's database URL: keep passwords out of URLs (`PGPASSWORD`/`.pgpass`).
- Found by the shared-mode tests and fixed: read queries left PostgreSQL connections idle-in-transaction (they hold locks and blocked DDL); concurrent `CREATE SCHEMA` and SQLite `ADD COLUMN` raced when several workers started together.
- Isolation is enforced in application code, not by the database. A future query that forgets `tenant_id` would leak across customers; the identical-data tests are the guard, so extend them with every new table.

## Deployment

One container (`Dockerfile`, non-root, `requirements-server.txt`), nothing kept on its disk. Full guide, environment table and steps for Fly.io (`fly.toml`, deploys from this folder) and Render (`render.yaml`, needs a GitHub repo): `deploy/README_DEPLOY.md`.
- `DUECLOCK_ENV=production`: the server **refuses to start** on an unsafe setup (no https public URL, missing or weak `DUECLOCK_TOKEN_SECRET`, control database not in PostgreSQL or without `?schema=`, transaction pooler port 6543, no database password, WhatsApp half-configured). `python3 -m dueclock.core.cli preflight [--probe]` lists FAIL/WARN/OK without printing secrets and optionally connects to both databases.
- `DUECLOCK_SCHEDULER=1`: reminders run inside the server every `DUECLOCK_SCHEDULER_MINUTES` (default 60); one customer's failure does not stop the others, and running on several servers is safe because each reminder step is claimed by a UNIQUE row first.
- `DUECLOCK_TRUST_PROXY=1`: honour `X-Forwarded-For` so login and scan rate limits are per visitor, not per proxy. `PORT` and `serve --host 0.0.0.0` for containers. `/v1/health` = process up, `/v1/ready` = control database reachable (use it as the host's health check).
- `scripts/smoke_production.sh`: boots the server from a clean virtualenv with only `requirements-server.txt`, production mode, real PostgreSQL; checks refusal on a bad config, health/ready, create-tenant, sync and read-back.
- Hosts: Fly.io (deployed and verified, but stops after 5 min without a card), Render free (`render.yaml`), Vercel Hobby (`api/index.py`, `vercel.json`, cron endpoint `/v1/cron/reminders` guarded by `CRON_SECRET`, transaction pooler supported since the schema is set per transaction). Details and caveats: `deploy/README_DEPLOY.md`.
- **Not verified:** Render and Vercel builds, HTTPS in front of the app on those hosts, and the WhatsApp webhook with Meta.

## Buyer pages in Hindi and Kannada

The buyer page (`/p/<token>`) now renders fully in English, Hindi or Kannada (`core/i18n.py`): a language bar at the top (`?lang=hi|kn|en`), the buyer's saved language (`PUT /v1/buyers/<id>/language`) when no choice is made, forms and error pages that keep the language, localized dates, `<html lang>` set, dispute reasons shown in the language but stored in English, everything still HTML-escaped under the same strict CSP. All 47 strings and the 8 WhatsApp template bodies exist in all three languages (tests check identical placeholders and that the text is really in the right script).
- **The Hindi and Kannada text is machine-drafted by the software author and is NOT reviewed.** `i18n.REVIEWED_LANGUAGES` is `("en",)`; a native speaker (and the CA reviewer for the tax and MSMED sentences) must review it before customers see it or Meta receives it. The tracker rows T-09..T-18 in `blueprint/15_WhatsApp_Template_Tracker.xlsx` now hold the drafted bodies, status Draft, reviewer "NEEDED". S6, S7 and P1 in hi/kn exist in code (`i18n.WA_BODIES`) but have no tracker rows yet.
- WhatsApp templates in hi/kn are used only for languages listed in `approved_languages` (default English only), after Meta approves them.
- Found by the cross-check: the S5 template sent 5 variables but its approved body has 4 (Meta would reject it); fixed.
- Not translated: the owner screen, the exposure scan and the privacy page (English).

## Business profile (supplier details)

Settings now has a **Your business** form (name, Micro/Small/Medium/Large, Manufacturing/Services/Trading, Udyam number and registration date, GSTIN, UPI ID, phone, email) with field-level validation (`core/validate.py`: UDYAM-XX-00-0000000, GSTIN pattern, UPI ID, Indian mobile, date not in the future) and a plain-language "what this means" preview (covered / trader / medium-large / unknown, missing registration date, invoices dated before registration, no UPI ID). Invalid input re-renders with messages and keeps what was typed. A banner on Collections shows until category and activity are set. `PUT /v1/supplier` validates with the same rules (422 with field messages; unchanged profile on error), the save is atomic, and the owner-screen save is audited. Open: whether the registration date must precede supply is still the reviewer question (REGISTERED_AFTER_SUPPLY stays a review flag).

## Real Tally check

`dueclock-connector tally-check [--company X] [--save-profile] [--out-dir D] [--no-redact]` runs on the customer's PC against the real TallyPrime (the installer runs it once, best effort). Read-only throughout (same guard as every request). Stages: reachability and product; company list (3 request variants); bills receivable (2 built-in variants plus an optional on-site request file `tally_bills_template_path`, `{company}` placeholder); parse statistics (bills, buyers, total pending, duplicates, credit-days inference, original amounts missing); verdict PASS / PARTIAL / FAIL with coded findings (`TALLY_UNREACHABLE`, `PARSER_SHAPE_UNKNOWN` lists the tags Tally sent, `ZERO_BILLS`, `DUPLICATE_BILLS`...). `--save-profile` writes the variants that worked into the config (`tally_profile`) and the connector then uses them.
Captures are **redacted by default**: names, references and free text become stable pseudonyms; dates, amounts and structure stay, so a capture is safe to share. A redacted capture saved as `tests/fixtures/tally/real_<version>.xml` is replayed through the parser by `test_real_capture_parses` (skipped until one exists). **Rehearse without Tally:** `python3 -m dueclock.connector.demo_tally` runs a pretend Tally with fictional bills (`--utf16`, `--amount-style dr|negative`, `--plain-only` rehearse reply and fallback variations); it proves our code, not the real product. `dueclock-connector tally-fixture <capture> --name <label>` turns a real redacted capture into a permanent parser test. Step-by-step for the real PC: `packaging/windows/TALLY_CHECK_RUNBOOK.md`. Tested against a fake Tally server only; the real reply shapes are still unverified until this runs on a real TallyPrime (step 11 of `packaging/windows/SMOKE_TEST.md`).

## Reviewer test pack

`reviewer_pack/DueClock_Reviewer_Pack.xlsx` is built from the live engine (`core/review_pack.py`): 24 worked cases (inputs, the plain-English rule, what the engine says and why), 7 open questions, and the bank-rate rows, each with yellow reviewer columns (Agree/Disagree/Unsure, correct due date/interest, comment). Rebuild after any engine or rate change: `python3 -m dueclock.core.review_pack build reviewer_pack/DueClock_Reviewer_Pack.xlsx`.
When it comes back signed: `python3 -m dueclock.core.review_pack check signed.xlsx --out tests/core/reviewer_decisions.json`. That reports problems (blank or invalid cells, Disagree without a value, engine changed since issue), lists disagreements and exits 1 until complete. `tests/core/test_reviewer_decisions.py` then pins every Agree and fails on every Disagree until the engine matches the reviewer (skipped while the file is absent). Hand-calculated expectations for the cases are in `tests/core/test_review_pack.py`.

## Bank-rate history

`core/rates.py` holds the RBI Bank Rate (= MSF rate) from Aug 2017 to **5.75% from 7 Oct 2026**, one row per change, with a source note. Only the 27 Mar 2020 (4.65%) and 22 May 2020 (4.25%) rows were read from RBI documents (`confirmed`); the rest come from consistent secondary sources and keep the `RATE_TABLE_UNCONFIRMED` flag until checked against RBI's Table 46. The table records `verified_through` (10 Nov 2026); more than 60 days past it the clock adds `RATE_TABLE_STALE` (next MPC: 2-4 Dec 2026). Update by adding a row to `BANK_RATE_HISTORY` and moving `HISTORY_VERIFIED_THROUGH`. The worked statutory examples in the QA plan use a flat 6.5% and are pinned to that in tests; the default table now gives different interest for current dates (e.g. 17.25% p.a. at a 5.75% bank rate, not 19.5%). Open: whether interest uses the rate on the due date (current default) or each period's rate (`RatePolicy.THROUGH_PERIOD`) is a reviewer call.

## Windows packaging (connector)

Everything is in `packaging/windows/`; the installer is built on Windows (not on this Mac): `.\packaging\windows\build.ps1`, or the `windows-connector` GitHub Actions workflow.
- PyInstaller makes `dueclock-connector.exe` (console) and `dueclock-connectorw.exe` (no window, used by the scheduled task). Frozen on macOS here as a smoke test: 37 MB, no web framework bundled.
- Inno Setup installs per user, no administrator rights, unsigned for pilots. The wizard asks for cloud address, access key, Tally port; it hands them to `setup --from-file` through a temp file (never the command line); the file is wiped after reading.
- The key is stored with Windows DPAPI (`api_key_protected: dpapi:...`), so a copied config will not work on another PC or user. `DUECLOCK_API_KEY` still overrides.
- A hidden Task Scheduler job runs `run-once` at sign-in and hourly (least privilege). `install-task --xml-only` prints the definition.
- New commands: `setup`, `install-task`, `remove-task`, `doctor`, `version`. Data lives in `%LOCALAPPDATA%\DueClock`.
- **Unverified (needs a Windows PC):** the DPAPI calls, `schtasks` registration, the Inno Setup script and the SmartScreen/antivirus behaviour. `SMOKE_TEST.md` is the checklist; items 2-3 decide the .NET switch (D-03).

## Next to build

Hindi and Kannada versions of the buyer page; build and install-test the Windows installer on a real PC (see `packaging/windows/SMOKE_TEST.md`).
