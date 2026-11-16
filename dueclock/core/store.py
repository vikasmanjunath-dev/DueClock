"""Storage: one database per customer, an API-key control plane, and a tenant-scoped repository.

Design (architecture decision D-22/D-23): each customer gets its own database. The same code runs on
SQLite files (pilots) or PostgreSQL (``postgresql://`` URL). Every table also carries ``tenant_id`` and every
query filters on it, so the move to one shared database with row-level security needs no data-model change.

Amounts are stored as integer paise. Dates are ISO text. Ids are text (uuid hex).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from typing import Any, Dict, List, Optional, Tuple

TENANT_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS suppliers (
        tenant_id TEXT PRIMARY KEY, business_name TEXT, gstin TEXT, udyam_number TEXT, category TEXT,
        activity TEXT, udyam_registration_date TEXT, upi_vpa TEXT, phone TEXT, email TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS buyers (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, name_key TEXT NOT NULL, gstin TEXT,
        pan TEXT, mobile TEXT, email TEXT, has_written_agreement INTEGER NOT NULL DEFAULT 0,
        opt_in_at TEXT, created_at TEXT, UNIQUE (tenant_id, name_key, gstin))""",
    """CREATE TABLE IF NOT EXISTS invoices (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, tally_guid TEXT NOT NULL, invoice_number TEXT NOT NULL,
        invoice_date TEXT NOT NULL, delivery_date TEXT, acceptance_date TEXT, buyer_id TEXT NOT NULL,
        amount_paise INTEGER NOT NULL, outstanding_paise INTEGER NOT NULL, agreed_credit_days INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL, promised_date TEXT, paused INTEGER NOT NULL DEFAULT 0, created_at TEXT, updated_at TEXT,
        UNIQUE (tenant_id, tally_guid))""",
    """CREATE TABLE IF NOT EXISTS sync_runs (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, connector_version TEXT, tally_version TEXT, os TEXT,
        started_at TEXT, finished_at TEXT, status TEXT, records_received INTEGER, inserted INTEGER, updated INTEGER,
        unchanged INTEGER, errors INTEGER, error_detail TEXT)""",
    """CREATE TABLE IF NOT EXISTS reminder_events (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, invoice_id TEXT NOT NULL, step TEXT NOT NULL, channel TEXT,
        status TEXT, sent_at TEXT, detail TEXT)""",
    # The idempotency key of the whole reminder system: one row per invoice and step, enforced by the database, not by the caller.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_reminder_invoice_step ON reminder_events (invoice_id, step)",
    """CREATE TABLE IF NOT EXISTS buyer_links (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, invoice_id TEXT NOT NULL, created_at TEXT, expires_at TEXT NOT NULL,
        revoked INTEGER NOT NULL DEFAULT 0, view_count INTEGER NOT NULL DEFAULT 0, last_viewed_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS buyer_events (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, invoice_id TEXT NOT NULL, link_id TEXT, kind TEXT NOT NULL, payload TEXT, at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS tenant_settings (tenant_id TEXT NOT NULL, k TEXT NOT NULL, v TEXT, PRIMARY KEY (tenant_id, k))""",
    """CREATE TABLE IF NOT EXISTS bank_credits (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, txn_date TEXT NOT NULL, amount_paise INTEGER NOT NULL, utr TEXT, narration TEXT,
        payer_name TEXT, payer_vpa TEXT, source TEXT, status TEXT NOT NULL DEFAULT 'unmatched', matched_paise INTEGER NOT NULL DEFAULT 0,
        dedupe_key TEXT NOT NULL, imported_at TEXT, UNIQUE (tenant_id, dedupe_key))""",
    """CREATE TABLE IF NOT EXISTS matches (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, credit_id TEXT NOT NULL, status TEXT NOT NULL, score REAL, method TEXT, reasons TEXT,
        allocations TEXT NOT NULL, created_at TEXT, confirmed_at TEXT, confirmed_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS payer_aliases (tenant_id TEXT NOT NULL, alias_key TEXT NOT NULL, buyer_id TEXT NOT NULL, PRIMARY KEY (tenant_id, alias_key))""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, actor TEXT, action TEXT, detail TEXT, at TEXT)""",
]
MIGRATIONS = [
    ("invoices", "unposted_paise", "INTEGER DEFAULT 0"),
    ("buyers", "agreed_days", "INTEGER DEFAULT 0"), ("buyers", "language", "TEXT DEFAULT 'en'"), ("buyers", "opt_in_source", "TEXT"), ("buyers", "opted_out_at", "TEXT"),
    ("reminder_events", "provider_message_id", "TEXT"), ("reminder_events", "error", "TEXT"), ("reminder_events", "template", "TEXT"),
    ("reminder_events", "language", "TEXT"), ("reminder_events", "attempts", "INTEGER DEFAULT 0"),
    ("reminder_events", "next_attempt_at", "TEXT"), ("reminder_events", "created_at", "TEXT"),
]
CONTROL_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS message_index (message_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS scans (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, unlocked INTEGER NOT NULL DEFAULT 0, ip_hash TEXT, result TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS scan_leads (id TEXT PRIMARY KEY, email TEXT NOT NULL, consent_at TEXT NOT NULL, scan_id TEXT, ip_hash TEXT)""",
    """CREATE TABLE IF NOT EXISTS tenants (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, db_url TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT)""",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_paise(rupees: float) -> int:
    return int(round(float(rupees) * 100))


def to_rupees(paise: int) -> float:
    return round(paise / 100.0, 2)


SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def split_schema_param(url: str) -> Tuple[str, Optional[str]]:
    """`postgresql://u@h/db?schema=dueclock&sslmode=require` -> (url without `schema`, 'dueclock'). libpq would reject the unknown parameter."""
    parsed = urlparse(url)
    q = parse_qs(parsed.query, keep_blank_values=True)
    schema = (q.pop("schema", [None])[0])
    if schema is not None and not SCHEMA_NAME.match(schema):
        raise ValueError("schema must be lowercase letters, digits and underscores (max 41 characters)")
    return urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in q.items()}))), schema


def _plain(v: Any) -> Any:
    """PostgreSQL returns SUM() of bigint as Decimal; the rest of the code expects int/float."""
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


class Database:
    """Thin portable wrapper over sqlite3 or psycopg2. SQL uses ``?`` placeholders."""

    def __init__(self, url: str):
        self.url = url
        self.lock = threading.RLock()
        self._depth = 0
        self._tx_open = False                                # PostgreSQL: has this transaction already had its search_path set?
        if url.startswith(("postgres://", "postgresql://")):
            import psycopg2
            import psycopg2.extras
            self.kind = "pg"
            clean, self.schema = split_schema_param(url)
            self.conn = psycopg2.connect(clean)
            self._cursor_factory = psycopg2.extras.RealDictCursor
            if self.schema:                                  # shared database: all customers live in one dedicated schema
                with self.conn.cursor() as c:
                    c.execute("SELECT pg_advisory_xact_lock(727274)")          # concurrent CREATE SCHEMA IF NOT EXISTS races in the catalog
                    c.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
                self.conn.commit()
            # The schema is selected with SET LOCAL at the start of EVERY transaction (see _prefix), never per session, so this also works
            # through a transaction-mode pooler (Supabase port 6543), where consecutive transactions may run on different server connections.
        else:
            self.kind = "sqlite"
            path = url.replace("sqlite:///", "")
            if path != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys=ON")
            self._cursor_factory = None

    def _sql(self, sql: str) -> str:
        # psycopg2 treats every % as a parameter marker: literal percent signs (LIKE patterns) must be doubled first.
        return sql.replace("%", "%%").replace("?", "%s") if self.kind == "pg" else sql

    def _cur(self):
        return self.conn.cursor(cursor_factory=self._cursor_factory) if self.kind == "pg" else self.conn.cursor()

    def _prefix(self) -> str:
        """First statement of a PostgreSQL transaction carries `SET LOCAL search_path` (same round trip, simple-query protocol)."""
        if self.kind == "pg" and self.schema and not self._tx_open:
            self._tx_open = True
            return f'SET LOCAL search_path TO "{self.schema}"; '
        return ""

    def _commit(self) -> None:
        self.conn.commit()
        self._tx_open = False

    def _rollback(self) -> None:
        self.conn.rollback()
        self._tx_open = False

    @contextmanager
    def schema_lock(self):
        """Serialise schema setup across processes (PostgreSQL advisory lock inside one transaction); no-op on SQLite."""
        if self.kind != "pg":
            yield
            return
        with self.transaction():
            self.execute("SELECT pg_advisory_xact_lock(727274)")
            yield

    def lock_down(self) -> None:
        """PostgreSQL only. Our tables must never be reachable through a hosted REST layer (Supabase exposes `public` to its anon/authenticated roles):
        enable row-level security with no policies, and revoke those roles' privileges. Our own connection owns the tables and is unaffected."""
        if self.kind != "pg":
            return
        for r in self.query("SELECT c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = current_schema() AND c.relkind = 'r' AND NOT c.relrowsecurity"):          # only tables not yet locked: no needless DDL locks
            self.execute(f'ALTER TABLE "{r["t"]}" ENABLE ROW LEVEL SECURITY')
        self.execute("""DO $$
            DECLARE r text;
            BEGIN
              FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                  EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I', current_schema(), r);
                  EXECUTE format('REVOKE ALL ON SCHEMA %I FROM %I', current_schema(), r);
                END IF;
              END LOOP;
            END $$""")

    def _ddl(self, sql: str) -> str:
        """SQLite INTEGER is 64-bit; PostgreSQL INTEGER is 32-bit (paise overflow above ~Rs 2.1 crore) and REAL is 4-byte. Widen both."""
        if self.kind != "pg":
            return sql
        return re.sub(r"\bINTEGER\b", "BIGINT", re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql))

    def insert_unique(self, sql: str, params: Tuple = ()) -> bool:
        """INSERT that returns False on a UNIQUE violation (anything else raises). Never aborts an enclosing transaction (savepoint on PostgreSQL)."""
        with self.lock:
            cur = self._cur()
            if self.kind == "pg":
                import psycopg2.errors
                cur.execute(self._prefix() + "SAVEPOINT dc_unique")
                try:
                    cur.execute(self._sql(sql), params)
                except psycopg2.errors.UniqueViolation:
                    cur.execute("ROLLBACK TO SAVEPOINT dc_unique")
                    if self._depth == 0:
                        self._rollback()
                    return False
                except Exception:
                    if self._depth == 0:
                        self._rollback()
                    raise
                cur.execute("RELEASE SAVEPOINT dc_unique")
            else:
                try:
                    cur.execute(sql, params)
                except sqlite3.IntegrityError as e:
                    if "UNIQUE" in str(e).upper():
                        return False
                    raise
            if self._depth == 0:
                self._commit()
            return True

    def execute(self, sql: str, params: Tuple = ()) -> int:
        with self.lock:
            cur = self._cur()
            try:
                cur.execute(self._prefix() + self._sql(sql), params)
            except Exception:
                if self._depth == 0 and self.kind == "pg":
                    self._rollback()                      # never leave the connection in an aborted transaction
                raise
            if self._depth == 0:
                self._commit()
            return cur.rowcount

    def query(self, sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        with self.lock:
            cur = self._cur()
            try:
                cur.execute(self._prefix() + self._sql(sql), params)
                rows = [{k: _plain(v) for k, v in dict(r).items()} for r in cur.fetchall()]
            except Exception:
                if self._depth == 0 and self.kind == "pg":
                    self._rollback()
                raise
            if self.kind == "pg" and self._depth == 0:
                self._commit()                           # end the implicit read transaction: an idle-in-transaction connection holds locks and blocks DDL
            return rows

    def one(self, sql: str, params: Tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self):
        with self.lock:
            self._depth += 1
            try:
                yield self
            except Exception:
                self._depth -= 1
                self._rollback()
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self._commit()

    def init(self, schema: List[str]) -> None:
        for stmt in schema:
            self.execute(self._ddl(stmt))

    def add_column_if_missing(self, table: str, column: str, ddl: str) -> None:
        if self.kind == "pg":
            self.execute(self._ddl(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
        elif column not in {r["name"] for r in self.query(f"PRAGMA table_info({table})")}:
            try:
                self.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            except sqlite3.OperationalError as e:         # another worker added it between our check and our ALTER
                if "duplicate column name" not in str(e):
                    raise

    def close(self) -> None:
        self.conn.close()


def migrate_control(src_sqlite_path: str, dst_url: str) -> Dict[str, int]:
    """Copy a local control.db (tenants, message index, scans, leads) into another control database. Safe to repeat: existing rows are kept."""
    src = Database("sqlite:///" + src_sqlite_path)
    dst = Database(dst_url)
    with dst.schema_lock():
        dst.init(CONTROL_SCHEMA)
        dst.lock_down()
    counts: Dict[str, int] = {}
    for table in ("tenants", "message_index", "scans", "scan_leads"):
        rows = src.query(f"SELECT * FROM {table}")
        counts[table] = 0
        for r in rows:
            cols = list(r)
            n = dst.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) ON CONFLICT DO NOTHING", tuple(r[c] for c in cols))
            counts[table] += 1 if n else 0
    return counts


def db_check(url: str, control: bool = False) -> Dict[str, Any]:
    """Open a database URL, set it up, and report what a deployer needs to know. Never prints the URL (it may hold a password)."""
    db = Database(url)
    if control:
        with db.schema_lock():
            db.init(CONTROL_SCHEMA)
            db.lock_down()
    else:
        TenantStore(db, "dbcheck")
    out: Dict[str, Any] = {"kind": db.kind, "ok": True, "tables": len(CONTROL_SCHEMA if control else TENANT_SCHEMA), "database": "control" if control else "tenant"}
    if db.kind == "pg":
        out["server"] = db.one("SELECT version() AS v")["v"].split(",")[0]
        out["schema"] = db.one("SELECT current_schema() AS s")["s"]
        tables = db.query("SELECT c.relname AS t, c.relrowsecurity AS rls FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = current_schema() AND c.relkind = 'r'")
        out["tables_found"], out["tables_without_rls"] = len(tables), [t["t"] for t in tables if not t["rls"]]
        exposed = []
        for role in ("anon", "authenticated"):
            if db.one("SELECT 1 AS x FROM pg_roles WHERE rolname = ?", (role,)):
                if db.one(f"SELECT 1 AS x FROM information_schema.role_table_grants WHERE grantee = ? AND table_schema = current_schema() LIMIT 1", (role,)):
                    exposed.append(role)
        out["roles_with_table_access"] = exposed
        out["ok"] = not out["tables_without_rls"] and not exposed
        if out["schema"] == "public":
            out["warning"] = "tables are in the public schema, which hosted REST layers expose; prefer ?schema=dueclock"
    return out


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    name: str
    db_url: str


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


class ControlPlane:
    """Maps API keys to customers and their databases. Keys are random, shown once, stored hashed."""

    def __init__(self, data_dir: str, control_db_url: Optional[str] = None):
        """The control database holds API-key hashes, the tenant list and the exposure-scan leads (email addresses). By default a SQLite file in
        ``data_dir``; with ``control_db_url`` (or DUECLOCK_CONTROL_DB_URL) it lives in PostgreSQL, in its own non-public schema."""
        self.data_dir = data_dir
        os.makedirs(os.path.join(data_dir, "tenants"), exist_ok=True)
        url = control_db_url or os.environ.get("DUECLOCK_CONTROL_DB_URL") or "sqlite:///" + os.path.join(data_dir, "control.db")
        if url.startswith(("postgres://", "postgresql://")) and not split_schema_param(url)[1]:
            raise ValueError("the control database must use its own schema, e.g. postgresql://...?schema=dueclock_control (never the public schema: hosted REST layers expose it)")
        self.db = Database(url)
        with self.db.schema_lock():
            self.db.init(CONTROL_SCHEMA)
            self.db.lock_down()
        self._stores: Dict[str, "TenantStore"] = {}
        self._lock = threading.Lock()

    def create_tenant(self, name: str, db_url: Optional[str] = None) -> Tuple[str, str]:
        tenant_id = uuid.uuid4().hex[:16]
        api_key = "dc_" + secrets.token_urlsafe(32)
        db_url = db_url or os.environ.get("DUECLOCK_TENANT_DB_URL")
        if not db_url:
            if self.db.kind == "pg":
                # A hosted control database with customer data on one server's local disk is almost always a mistake (data lost with the disk, invisible to other servers).
                raise ValueError("no customer database given: set DUECLOCK_TENANT_DB_URL (or pass --db-url postgresql://...?schema=...). To really use a local file, pass --db-url sqlite:///path explicitly.")
            db_url = "sqlite:///" + os.path.join(self.data_dir, "tenants", f"{tenant_id}.db")
        tdb = Database(db_url)
        with tdb.schema_lock():
            tdb.init(TENANT_SCHEMA)
            tdb.lock_down()
        tdb.close()
        self.db.execute(
            "INSERT INTO tenants (id, name, key_hash, db_url, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (tenant_id, name, hash_key(api_key), db_url, now_iso()),
        )
        return tenant_id, api_key

    def authenticate(self, api_key: Optional[str]) -> Optional[TenantContext]:
        if not api_key:
            return None
        h = hash_key(api_key)
        row = self.db.one("SELECT id, name, key_hash, db_url FROM tenants WHERE key_hash = ? AND active = 1", (h,))
        if not row or not hmac.compare_digest(row["key_hash"], h):
            return None
        return TenantContext(row["id"], row["name"], row["db_url"])

    def rotate_key(self, tenant_id: str) -> str:
        api_key = "dc_" + secrets.token_urlsafe(32)
        self.db.execute("UPDATE tenants SET key_hash = ? WHERE id = ?", (hash_key(api_key), tenant_id))
        return api_key

    def deactivate(self, tenant_id: str) -> None:
        self.db.execute("UPDATE tenants SET active = 0 WHERE id = ?", (tenant_id,))

    def context_by_id(self, tenant_id: str) -> Optional[TenantContext]:
        row = self.db.one("SELECT id, name, db_url FROM tenants WHERE id = ? AND active = 1", (tenant_id,))
        return TenantContext(row["id"], row["name"], row["db_url"]) if row else None

    # ---- exposure scans (public, short-lived) and leads
    def save_scan(self, result_json: str, ip_hash: str, created_iso: str, expires_iso: str) -> str:
        sid = secrets.token_urlsafe(24)
        self.db.execute("INSERT INTO scans (id, created_at, expires_at, unlocked, ip_hash, result) VALUES (?,?,?,0,?,?)", (sid, created_iso, expires_iso, ip_hash, result_json))
        return sid

    def get_scan(self, scan_id: str, now_iso_: str) -> Optional[Dict[str, Any]]:
        return self.db.one("SELECT * FROM scans WHERE id = ? AND expires_at > ?", (scan_id, now_iso_))

    def unlock_scan(self, scan_id: str) -> None:
        self.db.execute("UPDATE scans SET unlocked = 1 WHERE id = ?", (scan_id,))

    def delete_scan(self, scan_id: str) -> bool:
        return bool(self.db.execute("DELETE FROM scans WHERE id = ?", (scan_id,)))

    def purge_scans(self, now_iso_: str) -> int:
        return self.db.execute("DELETE FROM scans WHERE expires_at <= ?", (now_iso_,))

    def count_scans_since(self, ip_hash: str, since_iso: str) -> int:
        return int(self.db.one("SELECT COUNT(*) AS n FROM scans WHERE ip_hash = ? AND created_at >= ?", (ip_hash, since_iso))["n"])

    def add_lead(self, email: str, scan_id: str, ip_hash: str, at_iso: str) -> None:
        self.db.execute("INSERT INTO scan_leads (id, email, consent_at, scan_id, ip_hash) VALUES (?,?,?,?,?)", (uuid.uuid4().hex, email, at_iso, scan_id, ip_hash))

    def index_message(self, message_id: str, tenant_id: str) -> None:
        self.db.execute("INSERT INTO message_index (message_id, tenant_id) VALUES (?, ?) ON CONFLICT (message_id) DO UPDATE SET tenant_id = excluded.tenant_id", (message_id, tenant_id))

    def tenant_for_message(self, message_id: str) -> Optional[str]:
        row = self.db.one("SELECT tenant_id FROM message_index WHERE message_id = ?", (message_id,))
        return row["tenant_id"] if row else None

    def list_tenants(self) -> List[Dict[str, Any]]:
        return self.db.query("SELECT id, name, active, created_at FROM tenants ORDER BY created_at")

    def store_for(self, ctx: TenantContext) -> "TenantStore":
        with self._lock:
            st = self._stores.get(ctx.tenant_id)
            if st is None:
                st = TenantStore(Database(ctx.db_url), ctx.tenant_id)
                self._stores[ctx.tenant_id] = st
            return st


class TenantStore:
    """Every method filters on ``tenant_id``; there is no unscoped query."""

    def __init__(self, db: Database, tenant_id: str):
        self.db = db
        self.tid = tenant_id
        with self.db.schema_lock():
            self.db.init(TENANT_SCHEMA)
            for table, column, ddl in MIGRATIONS:
                self.db.add_column_if_missing(table, column, ddl)
            self.db.lock_down()

    # ---- supplier
    def set_supplier(self, p: Dict[str, Any], actor: str = "system") -> None:
        with self.db.transaction():                                  # replace atomically: never leave a customer without a profile
            self.db.execute("DELETE FROM suppliers WHERE tenant_id = ?", (self.tid,))
            self.db.execute(
                "INSERT INTO suppliers (tenant_id, business_name, gstin, udyam_number, category, activity, "
                "udyam_registration_date, upi_vpa, phone, email, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self.tid, p.get("business_name"), p.get("gstin"), p.get("udyam_number"), (p.get("category") or "UNKNOWN").upper(),
                 (p.get("activity") or "UNKNOWN").upper(), p.get("udyam_registration_date"), p.get("upi_vpa"), p.get("phone"),
                 p.get("email"), now_iso()),
            )
            self.audit(actor, "supplier.set", p.get("business_name") or "")

    def get_supplier(self) -> Optional[Dict[str, Any]]:
        return self.db.one("SELECT * FROM suppliers WHERE tenant_id = ?", (self.tid,))

    # ---- buyers
    def upsert_buyer(self, name: str, gstin: Optional[str] = None, mobile: Optional[str] = None) -> str:
        key = " ".join(name.lower().split())
        row = self.db.one("SELECT id, mobile FROM buyers WHERE tenant_id = ? AND name_key = ? AND COALESCE(gstin,'') = ?",
                          (self.tid, key, gstin or ""))
        if row:
            if mobile and not row["mobile"]:
                self.db.execute("UPDATE buyers SET mobile = ? WHERE id = ? AND tenant_id = ?", (mobile, row["id"], self.tid))
            return row["id"]
        bid = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO buyers (id, tenant_id, name, name_key, gstin, mobile, has_written_agreement, created_at) VALUES (?,?,?,?,?,?,0,?)",
            (bid, self.tid, name.strip(), key, gstin, mobile, now_iso()),
        )
        return bid

    def get_buyer(self, buyer_id: str) -> Optional[Dict[str, Any]]:
        return self.db.one("SELECT * FROM buyers WHERE id = ? AND tenant_id = ?", (buyer_id, self.tid))

    def set_buyer_agreement(self, buyer_id: str, flag: bool, actor: str, days: Optional[int] = None) -> bool:
        n = self.db.execute("UPDATE buyers SET has_written_agreement = ?, agreed_days = ? WHERE id = ? AND tenant_id = ?",
                            (1 if flag else 0, int(days or 0) if flag else 0, buyer_id, self.tid))
        if n:
            self.audit(actor, "buyer.agreement", f"{buyer_id}={flag} days={days}")
        return bool(n)

    # ---- invoices
    def upsert_invoice(self, rec: Dict[str, Any]) -> Tuple[str, str]:
        """Insert or update by tally_guid. Returns (outcome, invoice_id); outcome is inserted/updated/unchanged."""
        existing = self.db.one("SELECT * FROM invoices WHERE tenant_id = ? AND tally_guid = ?", (self.tid, rec["tally_guid"]))
        amount, pending = to_paise(rec["amount"]), min(to_paise(rec["amount"]), to_paise(rec["pending_amount"]))
        if existing and not rec.get("amount_known", True):
            amount = max(existing["amount_paise"], to_paise(rec["pending_amount"]))   # original amount stays; only the balance moves
            pending = min(amount, to_paise(rec["pending_amount"]))
        status = "PAID" if pending <= 0 else ("PARTIALLY_PAID" if pending < amount else "PENDING")
        if existing:
            if existing["status"] == "DISPUTED" and pending > 0:
                status = "DISPUTED"
            old_unposted = int(existing.get("unposted_paise") or 0)
            decrease = max(0, existing["outstanding_paise"] - pending)           # Tally now reflects some of what we matched
            new_unposted = min(max(0, old_unposted - decrease), pending)
            fields = (rec["invoice_number"], rec["invoice_date"], rec.get("delivery_date"), rec["buyer_id"], amount, pending,
                      int(rec.get("agreed_credit_days") or 0), status, new_unposted)
            current = (existing["invoice_number"], existing["invoice_date"], existing["delivery_date"], existing["buyer_id"],
                       existing["amount_paise"], existing["outstanding_paise"], existing["agreed_credit_days"], existing["status"], old_unposted)
            if fields == current:
                return "unchanged", existing["id"]
            self.db.execute(
                "UPDATE invoices SET invoice_number=?, invoice_date=?, delivery_date=?, buyer_id=?, amount_paise=?, "
                "outstanding_paise=?, agreed_credit_days=?, status=?, unposted_paise=?, updated_at=? WHERE id=? AND tenant_id=?",
                fields + (now_iso(), existing["id"], self.tid),
            )
            return "updated", existing["id"]
        iid = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO invoices (id, tenant_id, tally_guid, invoice_number, invoice_date, delivery_date, buyer_id, "
            "amount_paise, outstanding_paise, agreed_credit_days, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, self.tid, rec["tally_guid"], rec["invoice_number"], rec["invoice_date"], rec.get("delivery_date"), rec["buyer_id"],
             amount, pending, int(rec.get("agreed_credit_days") or 0), status, now_iso(), now_iso()),
        )
        return "inserted", iid

    def get_invoice(self, invoice_id: str) -> Optional[Dict[str, Any]]:
        return self.db.one(
            "SELECT i.*, b.name AS buyer_name, b.has_written_agreement AS buyer_agreement, b.mobile AS buyer_mobile, "
            "b.opt_in_at, b.opted_out_at, b.language AS buyer_language, b.agreed_days AS buyer_agreed_days FROM invoices i JOIN buyers b ON b.id = i.buyer_id "
            "WHERE i.id = ? AND i.tenant_id = ?", (invoice_id, self.tid))

    def list_invoices(self, include_paid: bool = False) -> List[Dict[str, Any]]:
        sql = ("SELECT i.*, b.name AS buyer_name, b.has_written_agreement AS buyer_agreement, b.mobile AS buyer_mobile, "
               "b.opt_in_at, b.opted_out_at, b.language AS buyer_language, b.agreed_days AS buyer_agreed_days FROM invoices i JOIN buyers b ON b.id = i.buyer_id WHERE i.tenant_id = ?")
        if not include_paid:
            sql += " AND i.status <> 'PAID'"
        return self.db.query(sql + " ORDER BY i.invoice_date, i.invoice_number", (self.tid,))

    # ---- buyer links and events
    def create_link(self, invoice_id: str, expires_at_iso: str) -> str:
        lid = uuid.uuid4().hex
        self.db.execute("INSERT INTO buyer_links (id, tenant_id, invoice_id, created_at, expires_at) VALUES (?,?,?,?,?)",
                        (lid, self.tid, invoice_id, now_iso(), expires_at_iso))
        return lid

    def get_link(self, link_id: str) -> Optional[Dict[str, Any]]:
        return self.db.one("SELECT * FROM buyer_links WHERE id = ? AND tenant_id = ?", (link_id, self.tid))

    def list_links(self, invoice_id: str) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM buyer_links WHERE invoice_id = ? AND tenant_id = ? ORDER BY created_at", (invoice_id, self.tid))

    def revoke_links(self, invoice_id: str) -> int:
        return self.db.execute("UPDATE buyer_links SET revoked = 1 WHERE invoice_id = ? AND tenant_id = ?", (invoice_id, self.tid))

    def touch_link(self, link_id: str) -> None:
        self.db.execute("UPDATE buyer_links SET view_count = view_count + 1, last_viewed_at = ? WHERE id = ? AND tenant_id = ?",
                        (now_iso(), link_id, self.tid))

    def add_event(self, invoice_id: str, link_id: Optional[str], kind: str, payload: Optional[Dict[str, Any]] = None, at: Optional[str] = None) -> None:
        import json
        self.db.execute("INSERT INTO buyer_events (id, tenant_id, invoice_id, link_id, kind, payload, at) VALUES (?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex, self.tid, invoice_id, link_id, kind, json.dumps(payload or {}), at or now_iso()))

    def count_events(self, link_id: str, kinds: List[str], since_iso: str) -> int:
        marks = ",".join("?" for _ in kinds)
        row = self.db.one(f"SELECT COUNT(*) AS n FROM buyer_events WHERE tenant_id = ? AND link_id = ? AND kind IN ({marks}) AND at >= ?",
                          (self.tid, link_id, *kinds, since_iso))
        return int(row["n"])

    def events_for_invoice(self, invoice_id: str) -> List[Dict[str, Any]]:
        import json
        rows = self.db.query("SELECT kind, payload, at, link_id FROM buyer_events WHERE invoice_id = ? AND tenant_id = ? ORDER BY at", (invoice_id, self.tid))
        return [dict(r, payload=json.loads(r["payload"] or "{}")) for r in rows]

    def set_promise(self, invoice_id: str, promised: Optional[str]) -> bool:
        return bool(self.db.execute("UPDATE invoices SET promised_date = ?, updated_at = ? WHERE id = ? AND tenant_id = ?",
                                    (promised, now_iso(), invoice_id, self.tid)))

    def set_disputed(self, invoice_id: str) -> bool:
        return bool(self.db.execute("UPDATE invoices SET status = 'DISPUTED', updated_at = ? WHERE id = ? AND tenant_id = ? AND outstanding_paise > 0",
                                    (now_iso(), invoice_id, self.tid)))

    def resolve_dispute(self, invoice_id: str) -> bool:
        return bool(self.db.execute(
            "UPDATE invoices SET status = CASE WHEN outstanding_paise < amount_paise THEN 'PARTIALLY_PAID' ELSE 'PENDING' END, updated_at = ? "
            "WHERE id = ? AND tenant_id = ? AND status = 'DISPUTED'", (now_iso(), invoice_id, self.tid)))

    # ---- consent and settings
    def set_opt_in(self, buyer_id: str, source: str, at: Optional[str] = None) -> bool:
        n = self.db.execute("UPDATE buyers SET opt_in_at = ?, opt_in_source = ?, opted_out_at = NULL WHERE id = ? AND tenant_id = ?",
                            (at or now_iso(), source, buyer_id, self.tid))
        if n:
            self.audit("seller", "buyer.opt_in", f"{buyer_id} via {source}")
        return bool(n)

    def opt_out(self, buyer_id: str, at: Optional[str] = None) -> bool:
        n = self.db.execute("UPDATE buyers SET opted_out_at = ? WHERE id = ? AND tenant_id = ?", (at or now_iso(), buyer_id, self.tid))
        if n:
            self.audit("seller", "buyer.opt_out", buyer_id)
        return bool(n)

    def set_language(self, buyer_id: str, language: str) -> bool:
        return bool(self.db.execute("UPDATE buyers SET language = ? WHERE id = ? AND tenant_id = ?", (language, buyer_id, self.tid)))

    def get_setting(self, k: str, default: Optional[str] = None) -> Optional[str]:
        row = self.db.one("SELECT v FROM tenant_settings WHERE tenant_id = ? AND k = ?", (self.tid, k))
        return row["v"] if row else default

    def set_setting(self, k: str, v: str) -> None:
        self.db.execute("DELETE FROM tenant_settings WHERE tenant_id = ? AND k = ?", (self.tid, k))
        self.db.execute("INSERT INTO tenant_settings (tenant_id, k, v) VALUES (?,?,?)", (self.tid, k, v))

    def set_invoice_paused(self, invoice_id: str, paused: bool) -> bool:
        return bool(self.db.execute("UPDATE invoices SET paused = ? WHERE id = ? AND tenant_id = ?", (1 if paused else 0, invoice_id, self.tid)))

    # ---- reminder events (one row per invoice and step: the idempotency key)
    def claim_step(self, invoice_id: str, step: str, channel: str, template: str, language: str, at: str) -> Optional[str]:
        eid = uuid.uuid4().hex
        ok = self.db.insert_unique(
            "INSERT INTO reminder_events (id, tenant_id, invoice_id, step, channel, status, template, language, attempts, created_at) "
            "VALUES (?,?,?,?,?,'sending',?,?,0,?)", (eid, self.tid, invoice_id, step, channel, template, language, at))
        return eid if ok else None                                 # False: someone already claimed this step

    def update_event(self, event_id: str, **f: Any) -> None:
        if not f:
            return
        cols = ", ".join(f"{k} = ?" for k in f)
        self.db.execute(f"UPDATE reminder_events SET {cols} WHERE id = ? AND tenant_id = ?", (*f.values(), event_id, self.tid))

    def events_for_steps(self, invoice_id: str) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM reminder_events WHERE invoice_id = ? AND tenant_id = ?", (invoice_id, self.tid))

    def count_buyer_events_since(self, buyer_id: str, since_iso: str) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM reminder_events e JOIN invoices i ON i.id = e.invoice_id "
                          "WHERE e.tenant_id = ? AND i.buyer_id = ? AND e.created_at >= ? AND e.status <> 'failed'", (self.tid, buyer_id, since_iso))
        return int(row["n"])

    def due_retries(self, now_iso_: str) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM reminder_events WHERE tenant_id = ? AND status = 'retry' AND next_attempt_at <= ? ORDER BY created_at", (self.tid, now_iso_))

    def event_by_provider_id(self, message_id: str) -> Optional[Dict[str, Any]]:
        return self.db.one("SELECT * FROM reminder_events WHERE tenant_id = ? AND provider_message_id = ?", (self.tid, message_id))

    def paid_invoices_awaiting_receipt(self) -> List[Dict[str, Any]]:
        return self.db.query(
            "SELECT i.*, b.name AS buyer_name, b.mobile AS buyer_mobile, b.opt_in_at, b.opted_out_at, b.language AS buyer_language "
            "FROM invoices i JOIN buyers b ON b.id = i.buyer_id WHERE i.tenant_id = ? AND (i.status = 'PAID' OR i.outstanding_paise <= COALESCE(i.unposted_paise, 0)) "
            "AND EXISTS (SELECT 1 FROM reminder_events r WHERE r.invoice_id = i.id AND r.status IN ('sent','delivered','read') "
            "AND r.step NOT LIKE 'S6%' AND r.step <> 'S7') "
            "AND NOT EXISTS (SELECT 1 FROM reminder_events r2 WHERE r2.invoice_id = i.id AND r2.step = 'S7')", (self.tid,))

    def promise_events(self) -> List[Dict[str, Any]]:
        import json
        rows = self.db.query("SELECT invoice_id, payload, at FROM buyer_events WHERE tenant_id = ? AND kind = 'promise' ORDER BY at", (self.tid,))
        return [dict(r, payload=json.loads(r["payload"] or "{}")) for r in rows]

    def reminder_stats(self) -> Dict[str, Any]:
        rows = self.db.query("SELECT status, COUNT(*) AS n FROM reminder_events WHERE tenant_id = ? GROUP BY status", (self.tid,))
        by = {r["status"]: r["n"] for r in rows}
        delivered_or_better = sum(by.get(s, 0) for s in ("sent", "delivered", "read"))
        by_step = {r["step"]: r["n"] for r in self.db.query("SELECT step, COUNT(*) AS n FROM reminder_events WHERE tenant_id = ? GROUP BY step", (self.tid,))}
        return {"by_status": by, "by_step": by_step, "accepted": delivered_or_better,
                "open_rate": round(by.get("read", 0) / delivered_or_better, 4) if delivered_or_better else None}

    # ---- bank credits and matching
    def insert_credit(self, c: Dict[str, Any]) -> Optional[str]:
        cid = uuid.uuid4().hex
        ok = self.db.insert_unique(
            "INSERT INTO bank_credits (id, tenant_id, txn_date, amount_paise, utr, narration, payer_name, payer_vpa, source, status, matched_paise, dedupe_key, imported_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'unmatched',0,?,?)",
            (cid, self.tid, c["txn_date"], c["amount_paise"], c.get("utr"), c.get("narration"), c.get("payer_name"), c.get("payer_vpa"),
             c.get("source"), c["dedupe_key"], c.get("imported_at") or now_iso()))
        return cid if ok else None

    def get_credit(self, credit_id: str) -> Optional[Dict[str, Any]]:
        return self.db.one("SELECT * FROM bank_credits WHERE id = ? AND tenant_id = ?", (credit_id, self.tid))

    def list_credits(self, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM bank_credits WHERE tenant_id = ?", [self.tid]
        if status:
            sql += " AND status = ?"; args.append(status)
        return self.db.query(sql + " ORDER BY txn_date DESC, imported_at DESC LIMIT ?", (*args, limit))

    def update_credit(self, credit_id: str, **f: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in f)
        self.db.execute(f"UPDATE bank_credits SET {cols} WHERE id = ? AND tenant_id = ?", (*f.values(), credit_id, self.tid))

    def add_match(self, credit_id: str, status: str, score: float, method: str, reasons: List[str], allocations: List[Dict[str, Any]],
                  confirmed_by: Optional[str] = None, at: Optional[str] = None) -> str:
        import json
        mid = uuid.uuid4().hex
        self.db.execute("INSERT INTO matches (id, tenant_id, credit_id, status, score, method, reasons, allocations, created_at, confirmed_at, confirmed_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (mid, self.tid, credit_id, status, score, method, json.dumps(reasons), json.dumps(allocations), at or now_iso(),
                         at if status == "confirmed" else None, confirmed_by))
        return mid

    def _match(self, row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        import json
        return None if row is None else dict(row, reasons=json.loads(row["reasons"] or "[]"), allocations=json.loads(row["allocations"]))

    def get_match(self, match_id: str) -> Optional[Dict[str, Any]]:
        return self._match(self.db.one("SELECT * FROM matches WHERE id = ? AND tenant_id = ?", (match_id, self.tid)))

    def matches_for_credit(self, credit_id: str) -> List[Dict[str, Any]]:
        return [self._match(r) for r in self.db.query("SELECT * FROM matches WHERE credit_id = ? AND tenant_id = ? ORDER BY score DESC, created_at", (credit_id, self.tid))]

    def update_match(self, match_id: str, **f: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in f)
        self.db.execute(f"UPDATE matches SET {cols} WHERE id = ? AND tenant_id = ?", (*f.values(), match_id, self.tid))

    def delete_suggested_matches(self, credit_id: str) -> None:
        self.db.execute("DELETE FROM matches WHERE credit_id = ? AND tenant_id = ? AND status = 'suggested'", (credit_id, self.tid))

    def apply_unposted(self, invoice_id: str, delta_paise: int) -> None:
        self.db.execute("UPDATE invoices SET unposted_paise = MAX(0, MIN(outstanding_paise, COALESCE(unposted_paise,0) + ?)), updated_at = ? WHERE id = ? AND tenant_id = ?"
                        if self.db.kind == "sqlite" else
                        "UPDATE invoices SET unposted_paise = GREATEST(0, LEAST(outstanding_paise, COALESCE(unposted_paise,0) + ?)), updated_at = ? WHERE id = ? AND tenant_id = ?",
                        (delta_paise, now_iso(), invoice_id, self.tid))

    def matching_invoices(self) -> List[Dict[str, Any]]:
        return self.db.query(
            "SELECT i.id, i.invoice_number, i.invoice_date, i.buyer_id, b.name AS buyer_name, i.outstanding_paise - COALESCE(i.unposted_paise,0) AS effective_paise "
            "FROM invoices i JOIN buyers b ON b.id = i.buyer_id WHERE i.tenant_id = ? AND i.status <> 'PAID' AND i.outstanding_paise > COALESCE(i.unposted_paise,0) "
            "ORDER BY i.invoice_date, i.invoice_number", (self.tid,))

    def aliases(self) -> Dict[str, str]:
        return {r["alias_key"]: r["buyer_id"] for r in self.db.query("SELECT alias_key, buyer_id FROM payer_aliases WHERE tenant_id = ?", (self.tid,))}

    def put_alias(self, alias_key: str, buyer_id: str) -> None:
        self.db.execute("DELETE FROM payer_aliases WHERE tenant_id = ? AND alias_key = ?", (self.tid, alias_key))
        self.db.execute("INSERT INTO payer_aliases (tenant_id, alias_key, buyer_id) VALUES (?,?,?)", (self.tid, alias_key, buyer_id))

    def reconciliation_summary(self) -> Dict[str, Any]:
        rows = self.db.query("SELECT status, COUNT(*) AS n, COALESCE(SUM(amount_paise - matched_paise),0) AS open_paise FROM bank_credits WHERE tenant_id = ? GROUP BY status", (self.tid,))
        unposted = self.db.one("SELECT COALESCE(SUM(unposted_paise),0) AS p, COUNT(*) AS n FROM invoices WHERE tenant_id = ? AND COALESCE(unposted_paise,0) > 0", (self.tid,))
        return {"credits": {r["status"]: {"count": r["n"], "unallocated": to_rupees(r["open_paise"])} for r in rows},
                "matched_not_yet_in_tally": {"invoices": unposted["n"], "amount": to_rupees(unposted["p"])}}

    # ---- sync log and audit
    def record_sync(self, **k: Any) -> str:
        sid = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO sync_runs (id, tenant_id, connector_version, tally_version, os, started_at, finished_at, status, "
            "records_received, inserted, updated, unchanged, errors, error_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, self.tid, k.get("connector_version"), k.get("tally_version"), k.get("os"), k.get("started_at"), now_iso(),
             k.get("status"), k.get("received", 0), k.get("inserted", 0), k.get("updated", 0), k.get("unchanged", 0),
             k.get("errors", 0), k.get("error_detail")),
        )
        return sid

    def list_syncs(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM sync_runs WHERE tenant_id = ? ORDER BY finished_at DESC LIMIT ?", (self.tid, limit))

    def audit(self, actor: str, action: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO audit_log (id, tenant_id, actor, action, detail, at) VALUES (?,?,?,?,?,?)",
                        (uuid.uuid4().hex, self.tid, actor, action, detail, now_iso()))

    def audit_entries(self) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM audit_log WHERE tenant_id = ? ORDER BY at", (self.tid,))
