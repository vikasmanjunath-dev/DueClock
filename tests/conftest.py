"""Run the whole suite against PostgreSQL: set DUECLOCK_TEST_PG_URL to a server URL whose user may CREATE DATABASE, e.g.
    DUECLOCK_TEST_PG_URL=postgresql://postgres@127.0.0.1:54329/postgres python3 -m pytest -q
Every tenant created by a test then gets its own fresh PostgreSQL database (one database per customer), dropped at the end.
Set DUECLOCK_TEST_PG_SHARED=1 as well to put EVERY test tenant into one shared schema of one database instead (the Supabase-style deployment).
Set DUECLOCK_TEST_PG_CONTROL=1 to also keep each test's control database (API-key hashes, tenant list, scans) in its own fresh PostgreSQL schema.
Without it the control plane is a SQLite file. Tests that open an explicit sqlite:/// URL still use SQLite."""
import os
import uuid
from urllib.parse import urlparse, urlunparse

PG_URL = os.environ.get("DUECLOCK_TEST_PG_URL")
SHARED = bool(os.environ.get("DUECLOCK_TEST_PG_SHARED"))
CONTROL = bool(os.environ.get("DUECLOCK_TEST_PG_CONTROL"))
_ctl_schemas = []
_ctl_by_dir = {}
SHARED_SCHEMA = "dc_shared_" + uuid.uuid4().hex[:8]
_created = []

if PG_URL:
    import psycopg2
    from dueclock.core import store

    def _admin():
        c = psycopg2.connect(PG_URL)
        c.autocommit = True
        return c

    def _url_for(dbname: str) -> str:
        return urlunparse(urlparse(PG_URL)._replace(path="/" + dbname))

    _orig = store.ControlPlane.create_tenant

    def create_tenant(self, name, db_url=None):
        if db_url is None and SHARED:
            db_url = PG_URL + ("&" if "?" in PG_URL else "?") + "schema=" + SHARED_SCHEMA
        if db_url is None:
            dbname = "dc_t_" + uuid.uuid4().hex[:12]
            a = _admin()
            a.cursor().execute(f"CREATE DATABASE {dbname}")
            a.close()
            _created.append(dbname)
            db_url = _url_for(dbname)
        return _orig(self, name, db_url)

    store.ControlPlane.create_tenant = create_tenant
    store.ControlPlane._create_tenant_unpatched = _orig          # for tests of the real default behaviour

    if CONTROL:
        _orig_init = store.ControlPlane.__init__

        def __init__(self, data_dir, control_db_url=None):
            if control_db_url is None and not os.environ.get("DUECLOCK_CONTROL_DB_URL"):
                key = os.path.abspath(data_dir)                      # like control.db, the control schema belongs to a data directory
                if key not in _ctl_by_dir:
                    _ctl_by_dir[key] = "dc_ctl_" + uuid.uuid4().hex[:10]
                    _ctl_schemas.append(_ctl_by_dir[key])
                schema = _ctl_by_dir[key]
                control_db_url = PG_URL + ("&" if "?" in PG_URL else "?") + "schema=" + schema
            _orig_init(self, data_dir, control_db_url)

        store.ControlPlane.__init__ = __init__


def pytest_report_header(config):
    if not PG_URL:
        return "tenant databases: SQLite"
    return f"tenant databases: PostgreSQL ({urlparse(PG_URL).hostname}:{urlparse(PG_URL).port}), " + (f"ONE shared schema {SHARED_SCHEMA}" if SHARED else "one database per tenant") + (", control plane in PostgreSQL" if CONTROL else ", control plane SQLite")


def pytest_sessionfinish(session, exitstatus):
    if PG_URL and (_created or SHARED or _ctl_schemas):
        a = _admin()
        cur = a.cursor()
        for sch in _ctl_schemas:
            try:
                cur.execute(f'DROP SCHEMA IF EXISTS "{sch}" CASCADE')
            except Exception:
                pass
        if SHARED:
            try:
                cur.execute(f'DROP SCHEMA IF EXISTS "{SHARED_SCHEMA}" CASCADE')
            except Exception:
                pass
        for n in _created:
            try:
                cur.execute(f"DROP DATABASE IF EXISTS {n} WITH (FORCE)")
            except Exception:
                pass
        a.close()
