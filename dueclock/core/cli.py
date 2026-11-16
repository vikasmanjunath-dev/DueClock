"""Command line: create customers and run the core API.

    python -m dueclock.core.cli create-tenant "Precision Metal Works" --data-dir ./data
    python -m dueclock.core.cli list-tenants --data-dir ./data
    python -m dueclock.core.cli serve --data-dir ./data --port 8100
"""
import argparse
import os
import sys

from .store import ControlPlane


def _copy_to_clipboard(text: str) -> bool:
    import subprocess
    for cmd in (["pbcopy"], ["clip"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        try:
            subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def _deliver_key(label: str, key: str, copy: bool) -> None:
    if copy and _copy_to_clipboard(key):
        print(f"{label}: copied to the clipboard (not shown). Paste it into a password manager now; it cannot be displayed again.")
    else:
        if copy:
            print("could not reach the clipboard; showing the key instead", file=sys.stderr)
        print(f"{label} (shown once, store it safely): {key}")


def _explain_db_error(e: Exception) -> str:
    text = " ".join(str(e).split())
    hints = (("no password supplied", "no password. In this terminal run:  read -s PGPASSWORD; export PGPASSWORD   then try again"),
             ("password authentication failed", "wrong password (or wrong user). Check the user in Supabase's Connect screen; reset the password if unsure"),
             ("tenant or user not found", "wrong user or region. Copy the user and host exactly from Supabase's Connect screen (session pooler)"),
             ("could not translate host", "host name not found. Copy the host exactly from the Connect screen"),
             ("timeout", "timed out. Check your network; use the session pooler (port 5432), not the direct host"),
             ("must use its own schema", "the URL needs ?schema=... (a name other than public)"))
    for key, msg in hints:
        if key in text.lower():
            return msg
    return f"{type(e).__name__}: {text[:200]}"


def main() -> None:
    p = argparse.ArgumentParser(prog="dueclock-core")
    p.add_argument("command", choices=["create-tenant", "list-tenants", "rotate-key", "serve", "send-reminders", "db-check", "migrate-control", "deactivate-tenant", "preflight"])
    p.add_argument("name", nargs="?", help="customer name (create-tenant) or tenant id (rotate-key)")
    p.add_argument("--data-dir", default=os.environ.get("DUECLOCK_DATA_DIR", "./data"))
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--host", default="127.0.0.1", help="serve: address to listen on (0.0.0.0 inside a container)")
    p.add_argument("--probe", action="store_true", help="preflight: also connect to the databases")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--copy-key", action="store_true", help="create-tenant / rotate-key: copy the new API key to the clipboard and do NOT print it (keeps it out of screenshots and scrollback)")
    p.add_argument("--control", action="store_true", help="db-check: check the control database (DUECLOCK_CONTROL_DB_URL) instead of a tenant database")
    p.add_argument("--db-url", default=os.environ.get("DUECLOCK_TENANT_DB_URL"), help="create-tenant: put this customer on its own database (postgresql://user@host/dbname); default is a SQLite file. Keep passwords out of the URL (use PGPASSWORD or a .pgpass file): the URL is stored in the control database.")
    a = p.parse_args()
    if a.command == "serve":
        import uvicorn
        os.environ["DUECLOCK_DATA_DIR"] = a.data_dir
        trust = bool(os.environ.get("DUECLOCK_TRUST_PROXY"))
        uvicorn.run("dueclock.core.main:app", host=a.host, port=int(os.environ.get("PORT") or a.port),
                    proxy_headers=True, forwarded_allow_ips="*" if trust else None)     # "*" only when the app is reachable solely through the host's proxy
        return
    if a.command == "preflight":
        from .preflight import check_env, failures
        res = check_env(os.environ, probe_db=a.probe)
        for f in res:
            print(f"[{f['level'].upper():4}] {f['code']}: {f['message']}")
        sys.exit(1 if failures(res) else 0)
    if a.command == "migrate-control":
        from .store import migrate_control
        dst = os.environ.get("DUECLOCK_CONTROL_DB_URL")
        if not dst:
            p.error("set DUECLOCK_CONTROL_DB_URL to the destination control database")
        import json
        print(json.dumps(migrate_control(os.path.join(a.data_dir, "control.db"), dst), indent=2))
        return
    if a.command == "db-check":
        from .store import db_check
        url = os.environ.get("DUECLOCK_CONTROL_DB_URL") if a.control else a.db_url
        if not url:
            p.error("--db-url (or DUECLOCK_TENANT_DB_URL), or DUECLOCK_CONTROL_DB_URL with --control, required")
        import json
        try:
            res = db_check(url, control=a.control)
        except Exception as e:                                       # connection problems: one readable line, never the URL or a traceback
            print(f"cannot use this database: {_explain_db_error(e)}", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["ok"] else 1)
    cp = ControlPlane(a.data_dir)
    if a.command == "send-reminders":
        from dataclasses import asdict
        from .messaging import sender_from_env
        from .reminders import ReminderEngine
        from .tokens import TokenSigner, load_secret
        engine = ReminderEngine(sender_from_env(), TokenSigner(load_secret(a.data_dir)), public_base_url=os.environ.get("DUECLOCK_PUBLIC_URL", "http://127.0.0.1:8100"),
                                index_message=cp.index_message)
        for t in cp.list_tenants():
            if not t["active"] or (a.name and a.name != t["id"]):
                continue
            ctx = cp.context_by_id(t["id"])
            res = engine.run(cp.store_for(ctx), dry_run=a.dry_run)
            d = asdict(res)
            print(t["name"], {k: d[k] for k in ("status", "sent", "retried", "failed", "receipts", "confirmations")}, "decisions:", len(d["decisions"]))
        return
    if a.command == "create-tenant":
        if not a.name:
            p.error("customer name required")
        try:
            tid, key = cp.create_tenant(a.name, a.db_url)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"tenant id: {tid}")
        _deliver_key("API key", key, a.copy_key)
    elif a.command == "rotate-key":
        _deliver_key("new API key", cp.rotate_key(a.name), a.copy_key)
    elif a.command == "deactivate-tenant":
        if not a.name:
            p.error("tenant id required")
        if not any(t["id"] == a.name for t in cp.list_tenants()):
            print("no such tenant id", file=sys.stderr)
            sys.exit(1)
        cp.deactivate(a.name)
        print(f"tenant {a.name} deactivated: its API key no longer works (its data is kept)")
    else:
        for t in cp.list_tenants():
            print(t["id"], t["name"], "active" if t["active"] else "inactive", t["created_at"])


if __name__ == "__main__":
    main()
