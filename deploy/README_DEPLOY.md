# Deploying the DueClock server

The server is one container: `Dockerfile` (python 3.12, non-root, `requirements-server.txt` only). It keeps **nothing on its own disk**: customer data and API keys are in PostgreSQL (Supabase), the signing secret and credentials are environment variables. So it can be redeployed, moved or run twice freely.

## What is verified and what is not
- Verified: the server boots from a clean virtualenv with only `requirements-server.txt`, in production mode, against a real PostgreSQL, refuses an unsafe configuration, and stores data in the `dueclock` schema (`scripts/smoke_production.sh`).
- **Live on Fly.io (10 Nov 2026):** the Docker image built (47 MB), one machine runs in Tokyo (`nrt`) as app `dueclock`, `https://dueclock.fly.dev/v1/ready` answers `{"status":"ready"}` over HTTPS, and the server started in production mode (so every preflight FAIL check passed).
- NOT verified: `render.yaml`, `vercel.json` and the Vercel build (written from Vercel's docs; the schema-per-transaction change IS tested), the WhatsApp webhook against Meta, the Supabase session pooler under load, `fly ssh` preflight (SSH credentials not set up), and a restart/redeploy keeping buyer links valid.

## Environment (all hosts)
| Variable | Value | Secret? |
|---|---|---|
| `DUECLOCK_ENV` | `production` (the server then refuses to start on an unsafe setup) | no |
| `DUECLOCK_PUBLIC_URL` | the public **https** address, no path, e.g. `https://dueclock.fly.dev` | no |
| `DUECLOCK_CONTROL_DB_URL` | `postgresql://postgres.<ref>@aws-0-<region>.pooler.supabase.com:5432/postgres?schema=dueclock_control&sslmode=require` | no (no password in it) |
| `DUECLOCK_TENANT_DB_URL` | same with `?schema=dueclock` | no |
| `PGPASSWORD` | the database password | **yes** |
| `DUECLOCK_TOKEN_SECRET` | `openssl rand -hex 32`, **the same on every server, never changed** (changing it breaks every buyer link and owner session) | **yes** |
| `DUECLOCK_TRUST_PROXY` | `1` (only when the app is reachable solely through the host's proxy) | no |
| `DUECLOCK_SCHEDULER` / `_MINUTES` | `1` / `60`: reminders run inside the server. Several servers or repeated runs are safe (a UNIQUE database row claims each reminder step) | no |
| `DUECLOCK_WA_MODE=cloud`, `DUECLOCK_WA_PHONE_NUMBER_ID`, `DUECLOCK_WA_TOKEN`, `DUECLOCK_WA_APP_SECRET`, `DUECLOCK_WA_VERIFY_TOKEN` | WhatsApp Cloud API (not live until set: reminders use a mock sender and nothing reaches buyers) | token, app secret, verify token: **yes** |

Check an environment any time: `python3 -m dueclock.core.cli preflight --probe` (lists FAIL / WARN, never prints secrets, `--probe` also connects to both databases).

## Option A: Fly.io (deploys straight from this folder, no GitHub)
1. Install `flyctl` and run `fly auth login` yourself (your account, your card).
2. Edit `fly.toml`: set a unique `app = "..."`.
3. `fly launch --no-deploy --copy-config`  (keep the existing fly.toml)
4. Set secrets. Type the password at a hidden prompt, never in a command line that gets saved:
   ```
   read -s PW; fly secrets set PGPASSWORD="$PW"; unset PW
   fly secrets set DUECLOCK_TOKEN_SECRET="$(openssl rand -hex 32)" DUECLOCK_PUBLIC_URL="https://<app>.fly.dev" \
     DUECLOCK_CONTROL_DB_URL='postgresql://...?schema=dueclock_control&sslmode=require' DUECLOCK_TENANT_DB_URL='postgresql://...?schema=dueclock&sslmode=require'
   ```
   Save the generated token secret in a password manager: you cannot read it back.
5. `fly deploy`, then `curl https://<app>.fly.dev/v1/ready` should print `{"status":"ready"}`.
6. `fly ssh console -C "python -m dueclock.core.cli preflight --probe"` should end with READY.

## Option D: Vercel (no card; Hobby plan)
Vercel runs the app as one serverless function (`api/index.py`, routed by `vercel.json`), built from `requirements.txt`. No card is needed on the Hobby plan, and nothing sleeps: each request spins the function up (cold start about 1-3 s for the first request after idle). Read before choosing it:
- **Vercel's Hobby plan is for personal, non-commercial use** ([Vercel docs](https://vercel.com/docs/plans/hobby)). A pilot with real customers is commercial use: fine for building and demos, but move to Pro (paid) or another host before charging customers.
- **Reminders run from Vercel Cron, once a day** (Hobby plans allow one run per day; `vercel.json` schedules 04:00 UTC = 09:30 IST, inside quiet hours). The in-process scheduler cannot run on serverless. Vercel calls `/v1/cron/reminders` with `Authorization: Bearer $CRON_SECRET`: set the `CRON_SECRET` environment variable (32+ random characters) in the project. One run a day is enough for the S1-S5 cadence (it sends whatever is due) but same-day follow-ups wait until the next morning.
- **Database connection:** use Supabase's **transaction pooler (port 6543)** so hundreds of short-lived functions do not exhaust connections. The schema is now selected per transaction (`SET LOCAL`), so the pooler is safe (tested).
- **WhatsApp webhooks** work (they are plain HTTPS requests). The owner screen and buyer pages work. Nothing is written to disk.
- Functions are limited to 60 s here (`maxDuration`); a reminder run for a very large customer base may need splitting later.

Steps:
1. `npm i -g vercel` and `vercel login` (GitHub/email account, no card).
2. In this folder: `vercel link` (create a new project), then set the environment variables for Production in the dashboard or with `vercel env add NAME production` (it prompts for the value, nothing lands in your shell history): `DUECLOCK_ENV=production`, `DUECLOCK_PUBLIC_URL=https://<project>.vercel.app`, `DUECLOCK_CONTROL_DB_URL` and `DUECLOCK_TENANT_DB_URL` (port **6543**, `?schema=...&sslmode=require`), `PGPASSWORD`, `DUECLOCK_TOKEN_SECRET`, `CRON_SECRET`, later the WhatsApp values.
3. `vercel --prod`. Then `curl https://<project>.vercel.app/v1/ready` should print `{"status":"ready"}`.
4. The cron appears under the project's Settings > Cron Jobs; it can be triggered by hand there to test.

## Option C: no card at all (Render free + an uptime monitor)
Fly.io stops machines after 5 minutes until a card is added, so it cannot stay up without one. Render's own documentation says free web services need no card; third-party reports disagree, so check Render's pricing page before relying on it. Limits to accept:
- A free service **sleeps after 15 minutes without traffic** (about a minute to wake). While asleep, the reminder scheduler and WhatsApp webhooks do not run. Keep it awake by creating a free monitor at an uptime service (for example UptimeRobot, free account, no card) that requests `https://<service>.onrender.com/v1/ready` every 5 minutes.
- 750 free instance hours a month cover one always-awake service (about 744 hours). If the hours run out the service is suspended until next month.
- Free-plan terms change often. Treat this as a pilot setup, not a production one: move to a paid plan before depending on reminders for paying customers.
- The data and keys stay in Supabase, so nothing is lost when the service sleeps or is replaced.
Steps: as Option B below, but `render.yaml` already says `plan: free`.

## Option B: Render (deploys from a GitHub repo)
This folder is not a git repository yet. Create a **private** GitHub repository, then: `git init`, `git add .`, `git commit`, push (`.gitignore` already excludes `data/`, databases and `.env`). In Render choose New → Blueprint, point it at the repository (`render.yaml`), fill the secret values in the dashboard. Use a paid always-on plan: a sleeping free service misses reminders and webhooks.

## After it is live
1. Customers: run `create-tenant "Name" --copy-key` from your own machine (it uses the same two database URLs), or via the host's shell. The key goes to your clipboard and is never printed.
2. Connector: set `cloud_url` in each customer's connector setup to `DUECLOCK_PUBLIC_URL`; the Windows installer asks for it.
3. WhatsApp: in Meta's app settings set the webhook to `<DUECLOCK_PUBLIC_URL>/v1/webhooks/whatsapp` with your `DUECLOCK_WA_VERIFY_TOKEN`.
4. Watch the logs for `reminder run:` lines (one summary per hour).
5. Back up: Supabase's own backups cover the data; keep `DUECLOCK_TOKEN_SECRET` in a password manager.

## Region
Each request makes several database queries, so run the app near the database. The Supabase project is in ap-northeast-2 (Seoul). Fly has no Seoul region (`icn` is refused; `fly launch` silently picked Amsterdam). Use Tokyo, `nrt` (about 30-40 ms from Seoul; Mumbai would be about 110 ms per query): `fly.toml` says `nrt`, and machines must be cloned there with `fly machine clone <id> --region nrt`. `fly platform regions` lists what your account can use. On Render pick Singapore. Run ONE machine for a pilot; a second machine only doubles the cost, and the reminder scheduler is safe either way.
Note: `fly launch` rewrites `fly.toml` (quotes, extra fields); that is expected, keep the file it leaves.
