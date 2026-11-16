from datetime import date

import pytest
from fastapi.testclient import TestClient

from dueclock.core.api import create_core_app

TODAY = date(2027, 1, 20)


@pytest.fixture()
def env(tmp_path):
    app = create_core_app(str(tmp_path), today_fn=lambda: TODAY)
    c = TestClient(app)
    tid, key = app.state.control.create_tenant("Precision Metal Works")
    return app, c, {"X-API-Key": key}


def sync(c, h, records, **kw):
    return c.post("/v1/connector/sync", json={"connector_version": "0.1", "tally_version": "TallyPrime 4", "os": "Windows 11", "records": records, **kw}, headers=h)


REC = [
    dict(guid="a", invoice_number="INV/1", invoice_date="2027-01-01", delivery_date="2027-01-01", buyer_name="Zenith Tooling", amount=100000, pending_amount=100000),
    dict(guid="b", invoice_number="INV/2", invoice_date="2027-01-02", buyer_name="Kaveri Hydraulics", amount=50000, pending_amount=0),
]


def test_authentication_required(env):
    _, c, h = env
    assert c.get("/v1/invoices").status_code == 401
    assert c.get("/v1/invoices", headers={"X-API-Key": "dc_nope"}).status_code == 401
    assert c.post("/v1/connector/sync", json={"connector_version": "1", "records": []}).status_code == 401
    assert c.get("/v1/health").json()["status"] == "ok"


def test_end_to_end_sync_and_clock(env):
    _, c, h = env
    assert c.put("/v1/supplier", json={"business_name": "Precision", "category": "micro", "activity": "manufacturing", "udyam_registration_date": "2024-01-10", "upi_vpa": "pp@bank"}, headers=h).status_code == 200
    r = sync(c, h, REC)
    assert r.status_code == 200 and r.json()["inserted"] == 2 and r.json()["status"] == "ok"
    inv = c.get("/v1/invoices", headers=h).json()
    assert inv["as_of"] == "2027-01-20" and inv["count"] == 1                      # paid invoice hidden
    clock = inv["invoices"][0]["clock"]
    assert clock["due_date"] == "2027-01-16" and clock["overdue_days"] == 4 and clock["covered"] and clock["disallowance_exposure"]
    assert clock["interest"] == pytest.approx(191.67, abs=0.01)
    assert c.get("/v1/invoices?include_paid=true", headers=h).json()["count"] == 2


def test_agreement_flag_moves_due_date_and_is_audited(env):
    app, c, h = env
    c.put("/v1/supplier", json={"business_name": "P", "category": "MICRO", "activity": "MANUFACTURING"}, headers=h)
    sync(c, h, REC)
    item = c.get("/v1/invoices", headers=h).json()["invoices"][0]
    assert item["clock"]["effective_days"] == 15
    assert c.put(f"/v1/buyers/{item['buyer_id']}/agreement", json={"has_written_agreement": True}, headers=h).status_code == 200
    # connector sets 45 agreed days on the next sync; the flag survives it
    sync(c, h, [dict(REC[0], agreed_credit_days=45)])
    after = c.get(f"/v1/invoices/{item['id']}", headers=h).json()
    assert after["clock"]["due_date"] == "2027-02-15" and not after["clock"]["is_overdue"] and after["written_agreement"]
    assert c.put("/v1/buyers/nope/agreement", json={"has_written_agreement": True}, headers=h).status_code == 404


def test_trader_supplier_is_not_clocked(env):
    _, c, h = env
    c.put("/v1/supplier", json={"business_name": "T", "category": "SMALL", "activity": "TRADING"}, headers=h)
    sync(c, h, REC)
    clock = c.get("/v1/invoices", headers=h).json()["invoices"][0]["clock"]
    assert clock["coverage"] == "NOT_COVERED_TRADER" and clock["interest"] == 0 and not clock["disallowance_exposure"]


def test_customers_cannot_see_each_other(env):
    app, c, h = env
    _, key2 = app.state.control.create_tenant("Other Co")
    h2 = {"X-API-Key": key2}
    sync(c, h, REC)
    assert c.get("/v1/invoices?include_paid=true", headers=h2).json()["count"] == 0
    inv_id = c.get("/v1/invoices", headers=h).json()["invoices"][0]["id"]
    assert c.get(f"/v1/invoices/{inv_id}", headers=h2).status_code == 404
    buyer_id = c.get("/v1/invoices", headers=h).json()["invoices"][0]["buyer_id"]
    assert c.put(f"/v1/buyers/{buyer_id}/agreement", json={"has_written_agreement": True}, headers=h2).status_code == 404


def test_bad_records_reported_and_sync_logged(env):
    _, c, h = env
    r = sync(c, h, REC + [{"invoice_number": "X"}])
    assert r.json()["status"] == "partial" and len(r.json()["errors"]) == 1
    log = c.get("/v1/syncs", headers=h).json()["syncs"][0]
    assert log["connector_version"] == "0.1" and log["tally_version"] == "TallyPrime 4" and log["errors"] == 1
    assert c.post("/v1/connector/sync", json={"records": []}, headers=h).status_code == 422     # connector version required


def test_unknown_profile_is_flagged(env):
    _, c, h = env
    sync(c, h, REC)
    flags = c.get("/v1/invoices", headers=h).json()["invoices"][0]["clock"]["review_flags"]
    assert "SUPPLIER_PROFILE_INCOMPLETE" in flags and "RATE_TABLE_UNCONFIRMED" in flags
