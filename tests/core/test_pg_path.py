"""PostgreSQL-specific behaviour. Runs only when DUECLOCK_TEST_PG_URL is set (see tests/conftest.py); the whole rest of the suite also runs on PostgreSQL then."""
import os
import threading
import uuid
from decimal import Decimal

import pytest

from dueclock.core.store import ControlPlane, Database, TENANT_SCHEMA, TenantStore, now_iso

PG = os.environ.get("DUECLOCK_TEST_PG_URL")
SHARED = bool(os.environ.get("DUECLOCK_TEST_PG_SHARED"))
pytestmark = pytest.mark.skipif(not PG or SHARED, reason="set DUECLOCK_TEST_PG_URL (database-per-tenant mode) to run the PostgreSQL path tests; shared mode is covered by test_shared_db.py")


@pytest.fixture()
def tenant(tmp_path):
    cp = ControlPlane(str(tmp_path))
    tid, key = cp.create_tenant("PG Test Co")                       # conftest gives it a fresh PostgreSQL database
    ctx = cp.context_by_id(tid)
    return cp, ctx, TenantStore(Database(ctx.db_url), tid)


def seed_invoice(st, paise=100000, num="INV/1"):
    bid = uuid.uuid4().hex
    st.db.execute("INSERT INTO buyers (id, tenant_id, name, name_key, gstin, created_at) VALUES (?,?,?,?,?,?)", (bid, st.tid, "Buyer " + num, "buyer " + num.lower(), "", now_iso()))
    iid = uuid.uuid4().hex
    st.db.execute("INSERT INTO invoices (id, tenant_id, tally_guid, invoice_number, invoice_date, buyer_id, amount_paise, outstanding_paise, status, created_at, updated_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (iid, st.tid, "k" + iid, num, "2026-04-01", bid, paise, paise, "PENDING", now_iso(), now_iso()))
    return iid


def test_tenant_database_is_really_postgresql(tenant):
    _, ctx, st = tenant
    assert ctx.db_url.startswith("postgresql://") and st.db.kind == "pg"
    assert "PostgreSQL" in st.db.one("SELECT version() AS v")["v"]
    assert st.db.one("SELECT current_database() AS d")["d"].startswith("dc_t_")


def test_money_columns_are_64_bit(tenant):
    _, _, st = tenant
    types = {(r["table_name"], r["column_name"]): r["data_type"] for r in st.db.query(
        "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 'public'")}
    for key in [("invoices", "amount_paise"), ("invoices", "outstanding_paise"), ("invoices", "unposted_paise"), ("bank_credits", "amount_paise"), ("bank_credits", "matched_paise")]:
        assert types[key] == "bigint", key
    assert types[("matches", "score")] == "double precision"


def test_amounts_above_32_bit_paise_roundtrip(tenant):
    _, _, st = tenant
    big = 30_000_000_000                                           # Rs 30 crore in paise: overflows a 32-bit INTEGER
    iid = seed_invoice(st, big)
    assert st.db.one("SELECT amount_paise AS a FROM invoices WHERE id = ?", (iid,))["a"] == big
    cid = st.insert_credit({"txn_date": "2026-05-01", "amount_paise": big, "dedupe_key": "big1", "source": "csv"})
    assert cid and st.get_credit(cid)["amount_paise"] == big


def test_aggregates_return_plain_numbers_not_decimal(tenant):
    _, _, st = tenant
    seed_invoice(st, 3_000_000_000, "INV/A")
    seed_invoice(st, 3_000_000_000, "INV/B")
    row = st.db.one("SELECT SUM(amount_paise) AS s, COUNT(*) AS n FROM invoices")
    assert row["s"] == 6_000_000_000 and type(row["s"]) is int and type(row["n"]) is int
    assert not isinstance(st.reconciliation_summary()["matched_not_yet_in_tally"]["amount"], Decimal)


def test_percent_signs_in_literals_and_like_patterns(tenant):
    _, _, st = tenant
    seed_invoice(st)
    assert st.db.query("SELECT 'a%b' AS v") == [{"v": "a%b"}]
    assert st.db.query("SELECT invoice_number FROM invoices WHERE invoice_number LIKE 'INV/%' AND tenant_id = ?", (st.tid,)) == [{"invoice_number": "INV/1"}]


def test_unique_violation_does_not_abort_an_enclosing_transaction(tenant):
    _, _, st = tenant
    iid = seed_invoice(st)
    with st.db.transaction():
        a = st.claim_step(iid, "S1", "whatsapp", "t", "en", now_iso())
        dup = st.claim_step(iid, "S1", "whatsapp", "t", "en", now_iso())        # duplicate inside the transaction
        b = st.claim_step(iid, "S2", "whatsapp", "t", "en", now_iso())          # must still work afterwards
    assert a and dup is None and b
    assert {r["step"] for r in st.db.query("SELECT step FROM reminder_events WHERE invoice_id = ?", (iid,))} == {"S1", "S2"}


def test_unique_violation_outside_a_transaction_leaves_connection_usable(tenant):
    _, _, st = tenant
    iid = seed_invoice(st)
    assert st.claim_step(iid, "S1", "whatsapp", "t", "en", now_iso())
    assert st.claim_step(iid, "S1", "whatsapp", "t", "en", now_iso()) is None
    assert st.db.one("SELECT COUNT(*) AS n FROM reminder_events")["n"] == 1


def test_non_unique_errors_still_raise(tenant):
    _, _, st = tenant
    with pytest.raises(Exception):
        st.db.insert_unique("INSERT INTO reminder_events (id) VALUES (?)", (uuid.uuid4().hex,))      # NOT NULL violation is not 'already claimed'


def test_transaction_rolls_back_on_error(tenant):
    _, _, st = tenant
    iid = seed_invoice(st)
    with pytest.raises(RuntimeError):
        with st.db.transaction():
            st.claim_step(iid, "S1", "whatsapp", "t", "en", now_iso())
            raise RuntimeError("boom")
    assert st.db.one("SELECT COUNT(*) AS n FROM reminder_events")["n"] == 0


def test_concurrent_workers_claim_a_step_exactly_once(tenant):
    _, ctx, st = tenant
    iid = seed_invoice(st)
    wins, barrier = [], threading.Barrier(8)

    def worker():
        s = TenantStore(Database(ctx.db_url), ctx.tenant_id)                   # each worker has its own connection, like separate processes
        barrier.wait()
        if s.claim_step(iid, "S1", "whatsapp", "t", "en", now_iso()):
            wins.append(1)
        s.db.close()

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(wins) == 1 and st.db.one("SELECT COUNT(*) AS n FROM reminder_events WHERE invoice_id = ?", (iid,))["n"] == 1


def test_duplicate_bank_credit_is_ignored(tenant):
    _, _, st = tenant
    c = {"txn_date": "2026-05-01", "amount_paise": 500000, "dedupe_key": "same", "source": "csv"}
    assert st.insert_credit(c) and st.insert_credit(c) is None
    assert st.db.one("SELECT COUNT(*) AS n FROM bank_credits")["n"] == 1


def test_schema_init_and_migrations_are_idempotent_and_upgrade_old_tables(tenant):
    _, ctx, st = tenant
    TenantStore(Database(ctx.db_url), ctx.tenant_id)                           # re-open: no error
    st.db.execute("ALTER TABLE invoices DROP COLUMN unposted_paise")
    st.db.execute("ALTER TABLE buyers DROP COLUMN language")
    TenantStore(Database(ctx.db_url), ctx.tenant_id)                           # migrations add them back
    cols = {r["column_name"] for r in st.db.query("SELECT column_name FROM information_schema.columns WHERE table_name IN ('invoices','buyers')")}
    assert {"unposted_paise", "language"} <= cols


def test_two_tenants_are_separate_databases(tmp_path):
    cp = ControlPlane(str(tmp_path))
    (a, _), (b, _) = cp.create_tenant("A"), cp.create_tenant("B")
    ua, ub = cp.context_by_id(a).db_url, cp.context_by_id(b).db_url
    assert ua != ub
    sa, sb = TenantStore(Database(ua), a), TenantStore(Database(ub), b)
    seed_invoice(sa)
    assert sb.db.one("SELECT COUNT(*) AS n FROM invoices")["n"] == 0


def test_apply_unposted_uses_greatest_least(tenant):
    _, _, st = tenant
    iid = seed_invoice(st, 100000)
    st.apply_unposted(iid, 60000)
    st.apply_unposted(iid, 70000)                                              # capped at outstanding
    assert st.db.one("SELECT unposted_paise AS u FROM invoices WHERE id = ?", (iid,))["u"] == 100000
    st.apply_unposted(iid, -500000)                                            # floored at zero
    assert st.db.one("SELECT unposted_paise AS u FROM invoices WHERE id = ?", (iid,))["u"] == 0


def test_schema_is_selected_per_transaction_not_per_session(tenant):
    """Transaction-mode poolers (Supabase 6543) hand each transaction to a possibly different server connection: session-level
    SET search_path would be lost. We use SET LOCAL at the start of every transaction, so even after a RESET the next statement works."""
    _, ctx, st = tenant
    url = ctx.db_url + ("&" if "?" in ctx.db_url else "?") + "schema=dc_tx_" + uuid.uuid4().hex[:6]
    db = Database(url)
    TenantStore(db, "t")
    db.execute("INSERT INTO tenant_settings (tenant_id, k, v) VALUES (?,?,?)", ("t", "k", "v"))
    db.conn.cursor().execute("RESET search_path"); db.conn.commit()                       # simulate landing on a different pooled connection
    assert db.one("SELECT v FROM tenant_settings WHERE tenant_id = ?", ("t",))["v"] == "v"
    with db.transaction():
        db.conn.cursor().execute("RESET ALL")                                             # even mid-transaction nothing else is relied upon
        pass
    assert db.one("SELECT COUNT(*) AS n FROM tenant_settings")["n"] == 1
