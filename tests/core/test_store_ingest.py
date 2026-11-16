from datetime import date

import os

import pytest

from dueclock.core.ingest import ingest_sync
from dueclock.core.store import ControlPlane, Database, TenantStore, now_iso


def rec(n, amount=100000, pending=100000, buyer="Zenith Tooling", **kw):
    d = dict(guid=f"g{n}", invoice_number=f"INV/{n}", invoice_date="2027-01-01", buyer_name=buyer, amount=amount, pending_amount=pending)
    d.update(kw)
    return d


@pytest.fixture()
def cp(tmp_path):
    return ControlPlane(str(tmp_path))


def make(cp, name):
    tid, key = cp.create_tenant(name)
    ctx = cp.authenticate(key)
    return tid, key, cp.store_for(ctx)


def test_api_key_auth(cp):
    tid, key, _ = make(cp, "A")
    assert cp.authenticate(key).tenant_id == tid
    assert cp.authenticate("dc_wrong") is None and cp.authenticate(None) is None and cp.authenticate("") is None
    stored = cp.db.one("SELECT key_hash FROM tenants WHERE id = ?", (tid,))["key_hash"]
    assert key not in stored and len(stored) == 64          # only the hash is stored


def test_rotate_and_deactivate(cp):
    tid, key, _ = make(cp, "A")
    new = cp.rotate_key(tid)
    assert cp.authenticate(key) is None and cp.authenticate(new) is not None
    cp.deactivate(tid)
    assert cp.authenticate(new) is None


def test_each_customer_gets_its_own_database(cp):
    a, _, sa = make(cp, "A")
    b, _, sb = make(cp, "B")
    if not os.environ.get("DUECLOCK_TEST_PG_SHARED"):                       # in shared mode both customers deliberately use one database
        assert sa.db.url != sb.db.url
    ingest_sync(sa, {"records": [rec(1)]})
    assert sb.list_invoices(include_paid=True) == []


def test_sync_inserts_then_is_idempotent(cp):
    _, _, st = make(cp, "A")
    first = ingest_sync(st, {"connector_version": "0.1", "records": [rec(1), rec(2, buyer="Kaveri")]})
    assert (first["inserted"], first["updated"], first["unchanged"], first["errors"]) == (2, 0, 0, [])
    second = ingest_sync(st, {"connector_version": "0.1", "records": [rec(1), rec(2, buyer="Kaveri")]})
    assert (second["inserted"], second["updated"], second["unchanged"]) == (0, 0, 2)
    assert len(st.list_invoices()) == 2 and len(st.list_syncs()) == 2


def test_payment_updates_status(cp):
    _, _, st = make(cp, "A")
    ingest_sync(st, {"records": [rec(1)]})
    r = ingest_sync(st, {"records": [rec(1, pending=40000)]})
    assert r["updated"] == 1 and st.list_invoices()[0]["status"] == "PARTIALLY_PAID" and st.list_invoices()[0]["outstanding_paise"] == 4000000
    ingest_sync(st, {"records": [rec(1, pending=0)]})
    assert st.list_invoices() == [] and st.list_invoices(include_paid=True)[0]["status"] == "PAID"


def test_bad_records_are_reported_not_fatal(cp):
    _, _, st = make(cp, "A")
    r = ingest_sync(st, {"records": [rec(1), {"invoice_number": "", "invoice_date": "2027-01-01", "buyer_name": "X", "amount": 1, "pending_amount": 1}, rec(3, amount=-5), {"junk": 1}]})
    assert r["inserted"] == 1 and len(r["errors"]) == 3 and r["status"] == "partial"
    assert st.list_syncs()[0]["errors"] == 3


def test_all_bad_marks_failed(cp):
    _, _, st = make(cp, "A")
    assert ingest_sync(st, {"records": [{"x": 1}]})["status"] == "failed"
    assert ingest_sync(st, {"records": []})["status"] == "ok"


def test_pending_never_exceeds_amount(cp):
    _, _, st = make(cp, "A")
    ingest_sync(st, {"records": [rec(1, amount=1000, pending=5000)]})
    assert st.list_invoices()[0]["outstanding_paise"] == 100000


def test_seller_agreement_flag_survives_sync(cp):
    _, _, st = make(cp, "A")
    ingest_sync(st, {"records": [rec(1)]})
    buyer_id = st.list_invoices()[0]["buyer_id"]
    assert st.set_buyer_agreement(buyer_id, True, actor="seller")
    ingest_sync(st, {"records": [rec(1, pending=50000)]})
    assert st.get_buyer(buyer_id)["has_written_agreement"] == 1
    assert any(a["action"] == "buyer.agreement" for a in st.audit_entries())


def test_buyers_are_deduplicated_by_name_and_gstin(cp):
    _, _, st = make(cp, "A")
    ingest_sync(st, {"records": [rec(1, buyer="Zenith  Tooling"), rec(2, buyer="zenith tooling")]})
    assert len({i["buyer_id"] for i in st.list_invoices()}) == 1


def test_tenant_scoping_inside_one_database(tmp_path):
    """Even if two customers shared a database, every query is tenant-filtered."""
    from dueclock.core.store import Database, TenantStore
    db = Database("sqlite:///" + str(tmp_path / "shared.db"))
    a, b = TenantStore(db, "tenantA"), TenantStore(db, "tenantB")
    ingest_sync(a, {"records": [rec(1)]})
    inv_id = a.list_invoices()[0]["id"]
    assert b.list_invoices(include_paid=True) == [] and b.get_invoice(inv_id) is None
    assert not b.set_buyer_agreement(a.list_invoices()[0]["buyer_id"], True, "x")


def test_too_many_records_rejected(cp):
    _, _, st = make(cp, "A")
    with pytest.raises(ValueError):
        ingest_sync(st, {"records": [{}] * 5001})


def test_failed_transaction_rolls_back(cp):
    _, _, st = make(cp, "A")
    ingest_sync(st, {"records": [rec(1)]})
    with pytest.raises(RuntimeError):
        with st.db.transaction():
            st.upsert_buyer("Rollback Co")
            raise RuntimeError("boom")
    assert st.db.query("SELECT 1 FROM buyers WHERE name = 'Rollback Co' AND tenant_id = ?", (st.tid,)) == []


def test_claim_step_is_unique_in_the_database(tmp_path):
    """The reminder idempotency key must be enforced by a UNIQUE index, not only by callers checking first."""
    st = TenantStore(Database("sqlite:///" + str(tmp_path / "c.db")), "t1")
    assert st.claim_step("inv1", "S1", "whatsapp", "t", "en", now_iso())
    assert st.claim_step("inv1", "S1", "whatsapp", "t", "en", now_iso()) is None
    assert st.claim_step("inv1", "S2", "whatsapp", "t", "en", now_iso()) and st.claim_step("inv2", "S1", "whatsapp", "t", "en", now_iso())
    assert st.db.one("SELECT COUNT(*) AS n FROM reminder_events WHERE tenant_id = ?", ("t1",))["n"] == 3


def test_cli_create_tenant_accepts_a_database_url(tmp_path, capsys, monkeypatch):
    from dueclock.core import cli
    target = "sqlite:///" + str(tmp_path / "custom" / "acme.db")
    monkeypatch.setattr("sys.argv", ["dueclock-core", "create-tenant", "Acme", "--data-dir", str(tmp_path), "--db-url", target])
    cli.main()
    assert "tenant id:" in capsys.readouterr().out and (tmp_path / "custom" / "acme.db").exists()
    cp = ControlPlane(str(tmp_path))
    t = cp.list_tenants()[0]
    assert "db_url" not in t                                                       # listings never expose the URL (it may hold a password)
    assert cp.context_by_id(t["id"]).db_url == target


def test_db_check_connection_failure_is_one_readable_line(monkeypatch, capsys):
    pytest.importorskip("psycopg2")
    from dueclock.core import cli
    monkeypatch.setattr("sys.argv", ["dueclock-core", "db-check", "--db-url", "postgresql://user:secretpw@127.0.0.1:1/db?schema=x"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    err = capsys.readouterr().err
    assert e.value.code == 2 and err.startswith("cannot use this database:") and "Traceback" not in err and "secretpw" not in err


def test_explain_db_error_hints():
    from dueclock.core.cli import _explain_db_error
    assert "PGPASSWORD" in _explain_db_error(Exception("connection failed: fe_sendauth: no password supplied"))
    assert "Connect screen" in _explain_db_error(Exception('FATAL:  Tenant or user not found'))


def test_cli_copy_key_never_prints_the_key_and_deactivate_works(tmp_path, monkeypatch, capsys):
    from dueclock.core import cli
    copied = []
    monkeypatch.setattr(cli, "_copy_to_clipboard", lambda t: copied.append(t) or True)
    monkeypatch.setattr("sys.argv", ["dueclock-core", "create-tenant", "Fresh Co", "--data-dir", str(tmp_path), "--copy-key"])
    cli.main()
    out = capsys.readouterr().out
    key = copied[0]
    assert key.startswith("dc_") and key not in out and "copied to the clipboard" in out
    cp = ControlPlane(str(tmp_path))
    tid = cp.list_tenants()[0]["id"]
    assert cp.authenticate(key).tenant_id == tid
    monkeypatch.setattr("sys.argv", ["dueclock-core", "rotate-key", tid, "--data-dir", str(tmp_path), "--copy-key"])
    cli.main()
    new = copied[1]
    out2 = capsys.readouterr().out
    assert new != key and new not in out2 and cp.authenticate(key) is None and cp.authenticate(new).tenant_id == tid
    monkeypatch.setattr("sys.argv", ["dueclock-core", "deactivate-tenant", tid, "--data-dir", str(tmp_path)])
    cli.main()
    assert "deactivated" in capsys.readouterr().out and cp.authenticate(new) is None
    monkeypatch.setattr("sys.argv", ["dueclock-core", "deactivate-tenant", "nope", "--data-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main()


def test_cli_falls_back_to_printing_when_no_clipboard(tmp_path, monkeypatch, capsys):
    from dueclock.core import cli
    monkeypatch.setattr(cli, "_copy_to_clipboard", lambda t: False)
    monkeypatch.setattr("sys.argv", ["dueclock-core", "create-tenant", "Fresh Co", "--data-dir", str(tmp_path), "--copy-key"])
    cli.main()
    cap = capsys.readouterr()
    assert "dc_" in cap.out and "could not reach the clipboard" in cap.err
