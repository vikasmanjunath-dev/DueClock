"""Deployment preflight: is this environment safe to run DueClock in production? Never prints secret values.

    python3 -m dueclock.core.cli preflight            # checks the environment of the shell it runs in
Set DUECLOCK_ENV=production on the server: the app then refuses to start while any check FAILS.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urlparse

from .store import split_schema_param

WA_REQUIRED = ("DUECLOCK_WA_PHONE_NUMBER_ID", "DUECLOCK_WA_TOKEN", "DUECLOCK_WA_APP_SECRET", "DUECLOCK_WA_VERIFY_TOKEN")
WEAK_SECRETS = {"changeme", "secret", "password", "test", "dueclock", "0" * 32, "x" * 32}


def _f(level: str, code: str, message: str) -> Dict[str, str]:
    return {"level": level, "code": code, "message": message}


def _pg_url_findings(name: str, url: str, out: List[Dict[str, str]]) -> None:
    u = urlparse(url)
    if not url.startswith(("postgres://", "postgresql://")):
        return
    _, schema = split_schema_param(url)
    if not schema:
        out.append(_f("fail", f"{name}_SCHEMA", f"{name} must carry ?schema=<name> (never the public schema: hosted REST layers expose it)."))
    if u.password:
        out.append(_f("warn", f"{name}_PASSWORD_IN_URL", f"{name} contains a password. Prefer the PGPASSWORD secret so the URL (stored in the control database) holds none."))
    if "sslmode=" not in (u.query or ""):
        out.append(_f("warn", f"{name}_SSL", f"{name} has no sslmode; hosted databases should use sslmode=require."))


def check_env(env: Mapping[str, str], probe_db: bool = False) -> List[Dict[str, str]]:
    """Return findings: level fail (must fix), warn (should fix), ok."""
    out: List[Dict[str, str]] = []
    pub = env.get("DUECLOCK_PUBLIC_URL", "")
    p = urlparse(pub)
    if not pub:
        out.append(_f("fail", "PUBLIC_URL_MISSING", "DUECLOCK_PUBLIC_URL is not set: buyer links and cookies need the public https address of this server."))
    elif p.scheme != "https" or p.hostname in (None, "localhost", "127.0.0.1", "::1"):
        out.append(_f("fail", "PUBLIC_URL_NOT_HTTPS", "DUECLOCK_PUBLIC_URL must be the public https address (session cookies are only marked Secure for https)."))
    elif p.path not in ("", "/"):
        out.append(_f("warn", "PUBLIC_URL_PATH", "DUECLOCK_PUBLIC_URL has a path; it should be just https://host"))

    secret = env.get("DUECLOCK_TOKEN_SECRET", "")
    if not secret:
        out.append(_f("fail", "TOKEN_SECRET_MISSING", "DUECLOCK_TOKEN_SECRET is not set: a secret kept in a local file is lost on every redeploy, which breaks every buyer link and owner session."))
    elif len(secret) < 32 or secret.lower() in WEAK_SECRETS or len(set(secret)) < 8:
        out.append(_f("fail", "TOKEN_SECRET_WEAK", "DUECLOCK_TOKEN_SECRET is short or guessable: use 32+ random characters (openssl rand -hex 32)."))

    ctl, ten = env.get("DUECLOCK_CONTROL_DB_URL", ""), env.get("DUECLOCK_TENANT_DB_URL", "")
    if not ctl:
        out.append(_f("fail", "CONTROL_DB_MISSING", "DUECLOCK_CONTROL_DB_URL is not set: API keys and the customer list would live on this server's disk and vanish on redeploy."))
    elif not ctl.startswith(("postgres://", "postgresql://")):
        out.append(_f("fail", "CONTROL_DB_NOT_POSTGRES", "DUECLOCK_CONTROL_DB_URL must be a PostgreSQL URL in production."))
    else:
        _pg_url_findings("CONTROL_DB", ctl, out)
    if not ten:
        out.append(_f("warn", "TENANT_DB_MISSING", "DUECLOCK_TENANT_DB_URL is not set: customers created on this server will be refused (existing customers are unaffected)."))
    elif ten.startswith(("postgres://", "postgresql://")):
        _pg_url_findings("TENANT_DB", ten, out)
        if ctl and split_schema_param(ctl)[1] and split_schema_param(ctl) [1] == split_schema_param(ten)[1] and urlparse(ctl).path == urlparse(ten).path:
            out.append(_f("fail", "SAME_SCHEMA", "control and customer databases share one schema; use different schemas (dueclock_control / dueclock)."))
    else:
        out.append(_f("warn", "TENANT_DB_LOCAL", "DUECLOCK_TENANT_DB_URL is a local file: customer data would be lost with this server's disk."))
    if (ctl or ten).startswith(("postgres://", "postgresql://")) and not env.get("PGPASSWORD") and not urlparse(ctl or ten).password and not env.get("PGPASSFILE"):
        out.append(_f("fail", "DB_PASSWORD_MISSING", "no database password: set the PGPASSWORD secret."))

    mode = env.get("DUECLOCK_WA_MODE", "").lower()
    if mode == "cloud":
        missing = [k for k in WA_REQUIRED if not env.get(k)]
        if missing:
            out.append(_f("fail", "WA_INCOMPLETE", "WhatsApp is switched on but these are not set: " + ", ".join(missing)))
    else:
        out.append(_f("warn", "WA_NOT_LIVE", "DUECLOCK_WA_MODE is not 'cloud': reminders use the mock sender, nothing reaches buyers."))

    if str(env.get("DUECLOCK_SCHEDULER", "")).lower() not in ("1", "true", "yes") and not (env.get("DUECLOCK_CRON_SECRET") or env.get("CRON_SECRET")):
        out.append(_f("warn", "NO_REMINDER_SCHEDULER", "neither DUECLOCK_SCHEDULER=1 nor a cron secret (DUECLOCK_CRON_SECRET / CRON_SECRET for /v1/cron/reminders) is set: reminders only go out if something else calls `cli send-reminders`."))
    if env.get("VERCEL") and str(env.get("DUECLOCK_SCHEDULER", "")).lower() in ("1", "true", "yes"):
        out.append(_f("warn", "SCHEDULER_ON_SERVERLESS", "DUECLOCK_SCHEDULER=1 on a serverless host does nothing useful (the process does not stay alive); use the cron endpoint instead."))

    if not env.get("DUECLOCK_TRUST_PROXY"):
        out.append(_f("warn", "PROXY_NOT_TRUSTED", "DUECLOCK_TRUST_PROXY is not set: behind a proxy every visitor looks like one IP, so login and scan rate limits are shared by everyone. Set it to 1 when the app is reachable only through the host's proxy."))

    if probe_db and not any(f["level"] == "fail" for f in out):
        from .store import db_check
        for label, url, control in (("CONTROL_DB", ctl, True), ("TENANT_DB", ten, False)):
            if not url:
                continue
            try:
                res = db_check(url, control=control)
                out.append(_f("ok" if res["ok"] else "fail", f"{label}_CHECK", f"{label}: connected, {res.get('tables_found', '?')} tables, RLS " + ("on" if res["ok"] else f"PROBLEM {res}")))
            except Exception as e:                                           # noqa: BLE001
                from .cli import _explain_db_error
                out.append(_f("fail", f"{label}_CONNECT", f"{label}: cannot connect: {_explain_db_error(e)}"))
    if not any(f["level"] == "fail" for f in out):
        out.append(_f("ok", "READY", "no blocking problems found"))
    return out


def failures(findings: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [f for f in findings if f["level"] == "fail"]


def enforce_in_production(env: Mapping[str, str]) -> None:
    """Called at startup. Only DUECLOCK_ENV=production is strict, so development and tests are unaffected."""
    if env.get("DUECLOCK_ENV", "").lower() != "production":
        return
    bad = failures(check_env(env, probe_db=False))
    if bad:
        raise RuntimeError("refusing to start in production: " + "; ".join(f"{f['code']}: {f['message']}" for f in bad))
