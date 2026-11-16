"""The control plane (API-key hashes, tenant list, message index, scan leads) in a database of its own: SQLite file or PostgreSQL schema."""
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from dueclock.core import cli
from dueclock.core.api import create_core_app
from dueclock.core.store import ControlPlane, Database, db_check, migrate_control, split_schema_param

PG = os.environ.get("DUECLOCK_TEST_PG_URL")
UTC = timezone.utc


def mk(cp, name, tmp_path):
    """Create a tenant on an explicit local SQLite file (these tests are about the control database, not the customer data)."""
    return cp.create_tenant(name, "sqlite:///" + str(tmp_path / "cust" / (uuid.uuid4().hex + ".db")))


@pytest.fixture(params=["sqlite"] + (["pg"] if PG else []))
def ctl_url(request, tmp_path):
    if request.param == "sqlite":
        yield "sqlite:///" + str(tmp_path / "elsewhere" / "control.db")
        return
    schema = "dc_ct_" + uuid.uuid4().hex[:8]
    yield PG + ("&" if "?" in PG else "?") + "schema=" + schema
    import psycopg2
    c = psycopg2.connect(split_schema_param(PG)[0])
    c.autocommit = True
    c.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    c.close()


def test_tenants_and_keys_live_in_the_control_database(tmp_path, ctl_url):
    cp = ControlPlane(str(tmp_path / "d1"), ctl_url)
    tid, key = mk(cp, "Alpha", tmp_path)
    assert cp.authenticate(key).tenant_id == tid and cp.authenticate("dc_wrong") is None
    assert not os.path.exists(tmp_path / "d1" / "control.db")                      # nothing is written to the local data directory's control file
    row = cp.db.one("SELECT key_hash FROM tenants WHERE id = ?", (tid,))
    assert key not in row["key_hash"] and len(row["key_hash"]) >= 32               # only a hash is stored
    new = cp.rotate_key(tid)
    assert cp.authenticate(key) is None and cp.authenticate(new).tenant_id == tid
    cp.deactivate(tid)
    assert cp.authenticate(new) is None


def test_survives_losing_the_app_servers_disk(tmp_path, ctl_url):
    """A new server with an EMPTY data directory sees the same customers and keys: nothing that matters is on local disk."""
    tid, key = mk(ControlPlane(str(tmp_path / "server1"), ctl_url), "Alpha", tmp_path)
    fresh = ControlPlane(str(tmp_path / "server2_empty_disk"), ctl_url)
    assert fresh.authenticate(key).tenant_id == tid and [t["id"] for t in fresh.list_tenants()] == [tid]


def test_two_app_servers_share_one_control_database(tmp_path, ctl_url):
    a, b = ControlPlane(str(tmp_path / "a"), ctl_url), ControlPlane(str(tmp_path / "b"), ctl_url)
    tid, key = mk(a, "Alpha", tmp_path)
    assert b.authenticate(key).tenant_id == tid                                    # created on A, usable on B immediately
    b.deactivate(tid)
    assert a.authenticate(key) is None                                             # and revoked everywhere at once


def test_message_index_upsert_and_scans(tmp_path, ctl_url):
    cp = ControlPlane(str(tmp_path), ctl_url)
    cp.index_message("wamid.1", "t1")
    cp.index_message("wamid.1", "t2")                                              # re-index moves it, never duplicates or fails
    assert cp.tenant_for_message("wamid.1") == "t2" and cp.tenant_for_message("nope") is None
    cp.db.execute("INSERT INTO scan_leads (id, email, consent_at, scan_id, ip_hash) VALUES (?,?,?,?,?)", ("l1", "a@b.test", "2027-01-01", "s1", "h"))
    assert cp.db.one("SELECT COUNT(*) AS n FROM scan_leads")["n"] == 1


def test_postgres_control_database_must_not_use_the_public_schema(tmp_path):
    with pytest.raises(ValueError, match="its own schema"):
        ControlPlane(str(tmp_path), "postgresql://u@localhost:1/postgres")
    with pytest.raises(ValueError, match="its own schema"):
        ControlPlane(str(tmp_path), "postgresql://u@localhost:1/postgres?sslmode=require")


def test_environment_variable_selects_the_control_database(tmp_path, monkeypatch, ctl_url):
    monkeypatch.setenv("DUECLOCK_CONTROL_DB_URL", ctl_url)
    tid, key = mk(ControlPlane(str(tmp_path / "x")), "Alpha", tmp_path)
    monkeypatch.delenv("DUECLOCK_CONTROL_DB_URL")
    assert ControlPlane(str(tmp_path / "y"), ctl_url).authenticate(key).tenant_id == tid


def test_whole_app_runs_on_a_separate_control_database(tmp_path, ctl_url):
    now = datetime(2027, 1, 11, 5, 0, tzinfo=UTC)
    app = create_core_app(str(tmp_path / "d"), control_db_url=ctl_url, now_fn=lambda: now, today_fn=lambda: now.date(), public_base_url="http://b.test")
    c = TestClient(app, follow_redirects=False)
    tid, key = mk(app.state.control, "Alpha", tmp_path)
    h = {"X-API-Key": key}
    r = c.post("/v1/connector/sync", json={"connector_version": "0.1", "records": [dict(guid="g1", invoice_number="INV/1", invoice_date="2027-01-01", buyer_name="Zenith", amount=1000, pending_amount=1000)]}, headers=h)
    assert r.status_code == 200 and c.get("/v1/invoices", headers=h).json()["count"] == 1
    assert c.get("/v1/invoices", headers={"X-API-Key": "dc_bad"}).status_code == 401


def test_migrate_a_local_control_db_into_the_new_one(tmp_path, ctl_url):
    old = ControlPlane(str(tmp_path / "old"), "sqlite:///" + str(tmp_path / "old" / "control.db"))      # the original SQLite control.db
    tid, key = mk(old, "Alpha", tmp_path)
    old.index_message("wamid.9", tid)
    old.db.execute("INSERT INTO scan_leads (id, email, consent_at, scan_id, ip_hash) VALUES (?,?,?,?,?)", ("l1", "a@b.test", "2027-01-01", "s1", "h"))
    counts = migrate_control(str(tmp_path / "old" / "control.db"), ctl_url)
    assert counts == {"tenants": 1, "message_index": 1, "scans": 0, "scan_leads": 1}
    assert migrate_control(str(tmp_path / "old" / "control.db"), ctl_url) == {"tenants": 0, "message_index": 0, "scans": 0, "scan_leads": 0}      # repeatable
    new = ControlPlane(str(tmp_path / "new"), ctl_url)
    assert new.authenticate(key).tenant_id == tid and new.tenant_for_message("wamid.9") == tid         # the customer's existing key keeps working


def test_db_check_control_and_cli(tmp_path, ctl_url, monkeypatch, capsys):
    res = db_check(ctl_url, control=True)
    assert res["ok"] and res["database"] == "control" and res["tables"] == 4
    if res["kind"] == "pg":
        assert res["tables_without_rls"] == [] and res["roles_with_table_access"] == [] and res["tables_found"] == 4
    monkeypatch.setenv("DUECLOCK_CONTROL_DB_URL", ctl_url)
    monkeypatch.setattr("sys.argv", ["dueclock-core", "db-check", "--control"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0 and '"database": "control"' in capsys.readouterr().out


@pytest.mark.skipif(not PG, reason="PostgreSQL only")
def test_public_rest_roles_cannot_read_the_key_hashes(ctl_url):
    import psycopg2
    if not ctl_url.startswith("postgres"):
        pytest.skip("sqlite variant")
    clean, schema = split_schema_param(ctl_url)
    db = Database(ctl_url)
    from dueclock.core.store import CONTROL_SCHEMA
    with db.schema_lock():
        db.init(CONTROL_SCHEMA)
        db.lock_down()
    c = psycopg2.connect(clean)
    c.autocommit = True
    cur = c.cursor()
    cur.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF; END $$")
    cur.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO anon; GRANT SELECT ON "{schema}".tenants TO anon')        # a later careless grant...
    db2 = Database(ctl_url)
    with db2.schema_lock():
        db2.lock_down()                                                                                        # ...is revoked again the next time the app starts
    cur.execute(f"SELECT has_table_privilege('anon', '\"{schema}\".tenants', 'SELECT')")
    assert cur.fetchone() == (False,)
    c.close()


def test_a_hosted_control_database_refuses_to_put_customer_data_on_local_disk(tmp_path, ctl_url, monkeypatch):
    monkeypatch.delenv("DUECLOCK_TENANT_DB_URL", raising=False)
    cp = ControlPlane(str(tmp_path), ctl_url)
    real_create = getattr(ControlPlane, "_create_tenant_unpatched", ControlPlane.create_tenant)         # the test harness otherwise supplies a database
    if cp.db.kind == "pg":
        with pytest.raises(ValueError, match="no customer database given"):
            real_create(cp, "Oops")
        assert cp.list_tenants() == []                                       # nothing half-created
        monkeypatch.setenv("DUECLOCK_TENANT_DB_URL", "sqlite:///" + str(tmp_path / "x.db"))
        assert real_create(cp, "Fine")                                       # the environment variable is honoured
    else:
        assert cp.create_tenant("Local")                                     # SQLite control database: local files remain the default
