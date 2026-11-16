"""Several customers in ONE database (and, on PostgreSQL, one dedicated schema). Runs on a shared SQLite file always, and on a shared
PostgreSQL schema when DUECLOCK_TEST_PG_URL is set. Both tenants get IDENTICAL data so any query missing a tenant filter shows up as leakage."""
import os
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import pytest
from fastapi.testclient import TestClient

from dueclock.core.api import create_core_app
from dueclock.core.messaging import MockSender
from dueclock.core.reminders import IST
from dueclock.core.store import ControlPlane, Database, TenantStore, now_iso, split_schema_param

UTC = timezone.utc
PG = os.environ.get("DUECLOCK_TEST_PG_URL")
SUPPLIER = {"business_name": "Precision Metal Works", "category": "MICRO", "activity": "MANUFACTURING", "udyam_registration_date": "2024-01-10", "upi_vpa": "pp@icici"}
REC = lambda n, buyer, amt: dict(guid=f"g{n}", invoice_number=f"INV/{n}", invoice_date="2027-01-01", delivery_date="2027-01-01", buyer_name=buyer, buyer_mobile="+91 98450 12345", amount=amt, pending_amount=amt)
CSV = "Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt\n18/01/27,UPI/412345678901/ZENITH TOOLING/z@icici/INV/1,412345678901,,\"1,00,000.00\"\n"


@pytest.fixture(params=["sqlite"] + (["pg"] if PG else []))
def shared_url(request, tmp_path):
    if request.param == "sqlite":
        yield "sqlite:///" + str(tmp_path / "shared.db")
        return
    schema = "dc_sh_" + uuid.uuid4().hex[:8]
    yield PG + ("&" if "?" in PG else "?") + "schema=" + schema
    import psycopg2
    c = psycopg2.connect(PG)
    c.autocommit = True
    c.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    c.close()


class Two:
    def __init__(self, tmp_path, url):
        self.now = datetime(2027, 1, 11, 5, 0, tzinfo=UTC)
        self.sender = MockSender()
        self.app = create_core_app(str(tmp_path), today_fn=lambda: self.now.astimezone(IST).date(), now_fn=lambda: self.now, public_base_url="http://b.test", sender=self.sender)
        self.c = TestClient(self.app, follow_redirects=False)
        cp = self.app.state.control
        self.ta, ka = cp.create_tenant("Alpha", url)
        self.tb, kb = cp.create_tenant("Beta", url)
        self.ha, self.hb = {"X-API-Key": ka}, {"X-API-Key": kb}
        for h in (self.ha, self.hb):                                    # identical data in both tenants
            self.c.put("/v1/supplier", json=SUPPLIER, headers=h)
            assert self.c.post("/v1/connector/sync", json={"connector_version": "0.1", "records": [REC(1, "Zenith Tooling", 100000), REC(2, "Kaveri Hydraulics", 50000)]}, headers=h).status_code == 200

    def invoices(self, h):
        return {i["invoice_number"]: i for i in self.c.get("/v1/invoices?include_paid=true", headers=h).json()["invoices"]}


def test_same_data_in_two_tenants_stays_separate(tmp_path, shared_url):
    t = Two(tmp_path, shared_url)
    ia, ib = t.invoices(t.ha), t.invoices(t.hb)
    assert set(ia) == set(ib) == {"INV/1", "INV/2"}
    assert {i["id"] for i in ia.values()}.isdisjoint({i["id"] for i in ib.values()})
    assert {i["buyer_id"] for i in ia.values()}.isdisjoint({i["buyer_id"] for i in ib.values()})
    # B cannot read or act on A's objects by id
    for path in (f"/v1/invoices/{ia['INV/1']['id']}",):
        assert t.c.get(path, headers=t.hb).status_code == 404
    assert t.c.put(f"/v1/buyers/{ia['INV/1']['buyer_id']}/opt-in", json={"source": "pilot_note"}, headers=t.hb).status_code == 404
    assert t.c.post(f"/v1/invoices/{ia['INV/1']['id']}/pause", headers=t.hb).status_code in (403, 404)
    assert t.invoices(t.ha)["INV/1"].get("paused") in (False, 0, None)


def test_sync_in_one_tenant_does_not_touch_the_other(tmp_path, shared_url):
    t = Two(tmp_path, shared_url)
    t.c.post("/v1/connector/sync", json={"connector_version": "0.1", "records": [dict(REC(1, "Zenith Tooling", 100000), pending_amount=0)]}, headers=t.ha)
    assert t.invoices(t.ha)["INV/1"]["outstanding"] == 0
    assert t.invoices(t.hb)["INV/1"]["outstanding"] == 100000
    assert len(t.c.get("/v1/syncs", headers=t.ha).json()["syncs"]) == 2
    assert len(t.c.get("/v1/syncs", headers=t.hb).json()["syncs"]) == 1


def test_reminders_run_only_for_the_calling_tenant(tmp_path, shared_url):
    t = Two(tmp_path, shared_url)
    for h, tid in ((t.ha, t.ta), (t.hb, t.tb)):
        for inv in t.invoices(h).values():
            t.c.put(f"/v1/buyers/{inv['buyer_id']}/opt-in", json={"source": "pilot_note"}, headers=h)
    t.now = datetime(2027, 1, 17, 5, 0, tzinfo=UTC)                    # past the 15-day due date
    ra = t.c.post("/v1/reminders/run", headers=t.ha).json()
    sent_a = len(t.sender.sent)
    assert ra["sent"] >= 1 and sent_a >= 1
    store_b = t.app.state.control.store_for(t.app.state.control.authenticate(t.hb["X-API-Key"]))
    assert store_b.db.query("SELECT COUNT(*) AS n FROM reminder_events WHERE tenant_id = ?", (t.tb,))[0]["n"] == 0     # B untouched
    rb = t.c.post("/v1/reminders/run", headers=t.hb).json()
    assert rb["sent"] == ra["sent"] and len(t.sender.sent) == 2 * sent_a     # B sends its own, independent of A's identical claims


def test_payments_are_tenant_scoped_including_identical_bank_rows(tmp_path, shared_url):
    t = Two(tmp_path, shared_url)
    for h in (t.ha, t.hb):                                             # identical UTR / dedupe key in both tenants must both import
        r = t.c.post("/v1/credits/import", json={"format": "csv", "text": CSV}, headers=h)
        assert r.status_code == 200 and r.json()["imported"] == 1
    ca, cb = (t.c.get("/v1/credits", headers=h).json()["credits"] for h in (t.ha, t.hb))
    assert len(ca) == len(cb) == 1 and ca[0]["id"] != cb[0]["id"]
    assert t.c.post(f"/v1/credits/{ca[0]['id']}/ignore", headers=t.hb).status_code in (403, 404)
    again = t.c.post("/v1/credits/import", json={"format": "csv", "text": CSV}, headers=t.ha).json()
    assert again["imported"] == 0                                      # duplicate inside the same tenant is still ignored


def test_buyer_link_resolves_only_within_its_tenant(tmp_path, shared_url):
    t = Two(tmp_path, shared_url)
    ia, ib = t.invoices(t.ha)["INV/1"], t.invoices(t.hb)["INV/1"]
    link = t.c.post(f"/v1/invoices/{ia['id']}/link", headers=t.ha).json()["url"]
    page = t.c.get("/p/" + link.split("/p/")[1])
    assert page.status_code == 200 and "INV/1" in page.text
    assert t.c.post(f"/v1/invoices/{ia['id']}/link", headers=t.hb).status_code == 404
    assert t.c.post(f"/v1/invoices/{ib['id']}/link", headers=t.hb).status_code == 200


def test_store_level_isolation_for_every_listing(shared_url):
    db = Database(shared_url)
    a, b = TenantStore(db, "tA"), TenantStore(db, "tB")
    a.set_supplier({"business_name": "A Co", "category": "MICRO", "activity": "MANUFACTURING"})
    a.upsert_buyer("Zenith")
    a.insert_credit({"txn_date": "2027-01-18", "amount_paise": 100, "dedupe_key": "k", "source": "csv"})
    a.set_setting("x", "1")
    assert a.get_supplier()["business_name"] == "A Co" and a.get_setting("x") == "1"
    assert b.get_supplier() is None and b.get_setting("x") is None
    assert b.list_invoices(include_paid=True) == [] and b.list_credits() == []
    for tbl in ("suppliers", "buyers", "invoices", "bank_credits", "matches", "reminder_events", "buyer_links", "buyer_events", "payer_aliases", "audit_log", "sync_runs", "tenant_settings"):
        assert b.db.query(f"SELECT COUNT(*) AS n FROM {tbl} WHERE tenant_id = ?", ("tB",))[0]["n"] == 0, tbl


def test_two_processes_opening_the_same_shared_database_at_once(tmp_path, shared_url):
    """Schema setup races (CREATE TABLE IF NOT EXISTS / ADD COLUMN) must not fail when several workers start together."""
    errors, barrier = [], threading.Barrier(6)

    def worker(i):
        try:
            barrier.wait()
            TenantStore(Database(shared_url), f"t{i}")
        except Exception as e:                                          # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == [], errors


def test_split_schema_param_and_validation():
    assert split_schema_param("postgresql://u@h/db?schema=dueclock&sslmode=require") == ("postgresql://u@h/db?sslmode=require", "dueclock")
    assert split_schema_param("postgresql://u@h/db") == ("postgresql://u@h/db", None)
    with pytest.raises(ValueError):
        split_schema_param("postgresql://u@h/db?schema=x;drop table y")
    with pytest.raises(ValueError):
        split_schema_param('postgresql://u@h/db?schema="quoted"')


@pytest.mark.skipif(not PG, reason="PostgreSQL only")
def test_lock_down_enables_rls_and_revokes_hosted_roles(shared_url):
    import psycopg2
    if not shared_url.startswith("postgres"):
        pytest.skip("sqlite variant")
    TenantStore(Database(shared_url), "tX")
    clean, schema = split_schema_param(shared_url)
    c = psycopg2.connect(clean)
    c.autocommit = True
    cur = c.cursor()
    cur.execute("SELECT count(*) FROM pg_tables t JOIN pg_class k ON k.relname = t.tablename AND k.relnamespace = %s::regnamespace WHERE t.schemaname = %s AND NOT k.relrowsecurity", (schema, schema))
    assert cur.fetchone()[0] == 0                                       # every table has row-level security enabled
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'anon'")
    if cur.fetchone():
        cur.execute(f'SELECT has_table_privilege(\'anon\', \'"{schema}".invoices\', \'SELECT\'), has_schema_privilege(\'anon\', \'{schema}\', \'USAGE\')')
        assert cur.fetchone() == (False, False)                         # the public REST key's role cannot read our tables
    c.close()


def test_db_check_reports_a_healthy_shared_database(shared_url, capsys, monkeypatch):
    from dueclock.core import cli
    from dueclock.core.store import db_check
    res = db_check(shared_url)
    assert res["ok"] and res["kind"] == ("pg" if shared_url.startswith("postgres") else "sqlite")
    if res["kind"] == "pg":
        assert res["tables_without_rls"] == [] and res["roles_with_table_access"] == [] and res["schema"].startswith("dc_sh_") and "warning" not in res
    monkeypatch.setattr("sys.argv", ["dueclock-core", "db-check", "--db-url", shared_url])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0 and '"ok": true' in capsys.readouterr().out


@pytest.mark.skipif(not PG, reason="PostgreSQL only")
def test_db_check_warns_about_the_public_schema_and_catches_exposure():
    import psycopg2
    from dueclock.core.store import db_check
    clean, _ = split_schema_param(PG)
    c = psycopg2.connect(clean)
    c.autocommit = True
    cur = c.cursor()
    cur.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF; END $$")
    s = "dc_exp_" + uuid.uuid4().hex[:6]
    try:
        TenantStore(Database(PG + ("&" if "?" in PG else "?") + "schema=" + s), "t")
        cur.execute(f'GRANT USAGE ON SCHEMA "{s}" TO anon; GRANT SELECT ON "{s}".invoices TO anon')          # someone re-grants access later
        res = db_check(PG + ("&" if "?" in PG else "?") + "schema=" + s)
        assert res["ok"] and res["roles_with_table_access"] == []                                              # opening the database re-runs the lock-down, which revokes the grant again
        pub = db_check(clean)
        assert pub["schema"] == "public" and "warning" in pub
    finally:
        cur.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
        cur.execute("DROP TABLE IF EXISTS public.suppliers, public.buyers, public.invoices, public.sync_runs, public.reminder_events, public.buyer_links, public.buyer_events, public.tenant_settings, public.bank_credits, public.matches, public.payer_aliases, public.audit_log CASCADE")
        c.close()


def test_default_tenant_database_from_environment(tmp_path, monkeypatch, capsys, shared_url):
    from dueclock.core import cli
    monkeypatch.setenv("DUECLOCK_TENANT_DB_URL", shared_url)
    for name in ("Alpha", "Beta"):
        monkeypatch.setattr("sys.argv", ["dueclock-core", "create-tenant", name, "--data-dir", str(tmp_path)])
        cli.main()
    cp = ControlPlane(str(tmp_path))
    urls = {cp.context_by_id(t["id"]).db_url for t in cp.list_tenants()}
    assert urls == {shared_url}
