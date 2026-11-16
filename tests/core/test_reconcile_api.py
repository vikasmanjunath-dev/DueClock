from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from dueclock.core.api import create_core_app
from dueclock.core.messaging import MockSender
from dueclock.core.reminders import IST

UTC = timezone.utc
SUPPLIER = {"business_name": "Precision Metal Works", "category": "MICRO", "activity": "MANUFACTURING", "udyam_registration_date": "2024-01-10", "upi_vpa": "precision@icici"}


def rec(n, buyer, amount, d, pending=None, mobile="+91 98450 12345"):
    return dict(guid=f"g{n}", invoice_number=f"INV/{n}", invoice_date=d, delivery_date=d, buyer_name=buyer, buyer_mobile=mobile, amount=amount, pending_amount=amount if pending is None else pending)


RECS = [rec(1, "Zenith Tooling Systems Pvt Ltd", 100000, "2027-01-01"), rec(2, "Zenith Tooling Systems Pvt Ltd", 50000, "2027-01-05"),
        rec(3, "Kaveri Hydraulics LLP", 75000, "2027-01-02", mobile="+919886054321")]
CSV = """Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt
18/01/27,UPI/412345678901/ZENITH TOOLING SYSTEMS/zenith@icici/INV/1,412345678901,,"1,00,000.00"
19/01/27,NEFT-HDFCN52027011912345-KAVERI HYDRAULICS,HDFCN52027011912345,,73500.00
19/01/27,UPI/999999999999/RANDOM PERSON,,,12345.00
19/01/27,CASH WITHDRAWAL,,5000.00,
"""


class Env:
    def __init__(self, tmp_path, **kw):
        self.now = datetime(2027, 1, 11, 5, 0, tzinfo=UTC)
        self.sender = MockSender()
        self.app = create_core_app(str(tmp_path), today_fn=lambda: self.now.astimezone(IST).date(), now_fn=lambda: self.now, public_base_url="http://b.test", sender=self.sender, **kw)
        self.c = TestClient(self.app, follow_redirects=False)
        self.tid, key = self.app.state.control.create_tenant("Precision")
        self.h = {"X-API-Key": key}
        self.c.put("/v1/supplier", json=SUPPLIER, headers=self.h)
        self.sync(RECS)
        self.store = self.app.state.control.store_for(self.app.state.control.authenticate(key))

    def sync(self, records):
        assert self.c.post("/v1/connector/sync", json={"connector_version": "0.1", "records": records}, headers=self.h).status_code == 200

    def inv(self, number):
        return {i["invoice_number"]: i for i in self.c.get("/v1/invoices?include_paid=true", headers=self.h).json()["invoices"]}[number]

    def credits(self):
        return {c["amount"]: c for c in self.c.get("/v1/credits", headers=self.h).json()["credits"]}

    def post(self, path, body=None):
        return self.c.post(path, json=body, headers=self.h)

    def import_csv(self, text=CSV):
        return self.post("/v1/credits/import", {"format": "csv", "text": text})


@pytest.fixture()
def env(tmp_path):
    return Env(tmp_path)


def test_import_suggests_matches_and_needs_a_key(env):
    assert env.c.post("/v1/credits/import", json={"format": "csv", "text": CSV}).status_code == 401
    r = env.import_csv().json()
    assert (r["imported"], r["duplicates"], r["ignored_debits"], r["with_suggestions"]) == (3, 0, 1, 2)
    cs = env.credits()
    top = cs[100000.0]["matches"][0]
    assert cs[100000.0]["status"] == "suggested" and top["allocations"][0]["invoice_number"] == "INV/1" and top["score"] >= 0.95
    assert "AMOUNT_WITHIN_TDS_TOLERANCE" in cs[73500.0]["matches"][0]["reasons"]
    assert cs[12345.0]["matches"] == [] and cs[12345.0]["status"] == "unmatched"


def test_reimport_is_idempotent(env):
    env.import_csv()
    again = env.import_csv().json()
    assert again["imported"] == 0 and again["duplicates"] == 3 and len(env.credits()) == 3


def test_confirm_stops_the_clock_and_reminders_then_tally_catches_up(env):
    env.c.put(f"/v1/buyers/{env.inv('INV/1')['buyer_id']}/opt-in", json={"source": "pilot_note"}, headers=env.h)
    env.post("/v1/reminders/run")                                                    # S1 goes to Zenith on 11 Jan
    assert len(env.sender.sent) == 1
    env.import_csv()
    mid = env.credits()[100000.0]["matches"][0]["id"]
    env.now = datetime(2027, 1, 18, 5, 0, tzinfo=UTC)
    before = env.inv("INV/1")
    assert before["outstanding"] == 100000.0 and before["clock"]["is_overdue"]
    assert env.post(f"/v1/matches/{mid}/confirm").json()["status"] == "matched"
    after = env.inv("INV/1")
    assert (after["outstanding"], after["tally_outstanding"], after["unposted_payments"]) == (0.0, 100000.0, 100000.0)
    assert not after["clock"]["is_overdue"] and after["clock"]["interest"] == 0
    r = env.post("/v1/reminders/run").json()
    assert r["receipts"] == 1 and env.sender.sent[-1]["template"] == "dc_s7_payment_received_en"      # thanked, no more reminders
    assert env.c.get("/v1/reconciliation/summary", headers=env.h).json()["matched_not_yet_in_tally"] == {"invoices": 1, "amount": 100000.0}
    env.sync([rec(1, "Zenith Tooling Systems Pvt Ltd", 100000, "2027-01-01", pending=0)] + RECS[1:])       # accountant posts it in Tally
    assert env.c.get("/v1/reconciliation/summary", headers=env.h).json()["matched_not_yet_in_tally"] == {"invoices": 0, "amount": 0.0}
    assert env.inv("INV/1")["status"] == "PAID"


def test_unposted_follows_tally_partial_posting(env):
    env.import_csv()
    env.post(f"/v1/matches/{env.credits()[73500.0]['matches'][0]['id']}/confirm")
    assert env.inv("INV/3")["outstanding"] == 1500.0
    env.sync(RECS[:2] + [rec(3, "Kaveri Hydraulics LLP", 75000, "2027-01-02", pending=35000, mobile="+919886054321")])     # Tally shows only 40,000 posted
    i = env.inv("INV/3")
    assert (i["tally_outstanding"], i["unposted_payments"], i["outstanding"]) == (35000.0, 33500.0, 1500.0)
    env.sync(RECS[:2] + [rec(3, "Kaveri Hydraulics LLP", 75000, "2027-01-02", pending=1500, mobile="+919886054321")])      # remaining posted
    i = env.inv("INV/3")
    assert (i["unposted_payments"], i["outstanding"]) == (0.0, 1500.0)


def test_undo_restores_everything(env):
    env.import_csv()
    mid = env.credits()[100000.0]["matches"][0]["id"]
    env.post(f"/v1/matches/{mid}/confirm")
    undone = env.post(f"/v1/matches/{mid}/unconfirm").json()
    assert undone["status"] == "unmatched" and env.inv("INV/1")["outstanding"] == 100000.0
    assert env.post(f"/v1/matches/{mid}/unconfirm").status_code == 409


def test_double_confirm_reject_and_unknown(env):
    env.import_csv()
    mid = env.credits()[100000.0]["matches"][0]["id"]
    assert env.post(f"/v1/matches/{mid}/confirm").status_code == 200
    assert env.post(f"/v1/matches/{mid}/confirm").status_code == 409 and env.post(f"/v1/matches/{mid}/reject").status_code == 409
    assert env.post("/v1/matches/nope/confirm").status_code == 404
    m2 = env.credits()[73500.0]["matches"][0]["id"]
    assert env.post(f"/v1/matches/{m2}/reject").json()["status"] == "unmatched"
    assert env.inv("INV/3")["outstanding"] == 75000.0


def test_manual_assignment_guards(env):
    env.import_csv()
    cid = env.credits()[12345.0]["id"]
    inv2 = env.inv("INV/2")["id"]
    assert env.post(f"/v1/credits/{cid}/manual", {"allocations": [{"invoice_id": inv2, "amount": 20000}]}).status_code == 409        # more than the credit
    assert env.post(f"/v1/credits/{cid}/manual", {"allocations": [{"invoice_id": inv2, "amount": -1}]}).status_code == 422
    assert env.post(f"/v1/credits/{cid}/manual", {"allocations": [{"invoice_id": "nope", "amount": 10}]}).status_code == 404
    ok = env.post(f"/v1/credits/{cid}/manual", {"allocations": [{"invoice_id": inv2, "amount": 12345}]}).json()
    assert ok["status"] == "matched" and env.inv("INV/2")["outstanding"] == 50000 - 12345
    big = env.credits()[100000.0]["id"]
    assert env.post(f"/v1/credits/{big}/manual", {"allocations": [{"invoice_id": inv2, "amount": 50000}]}).status_code == 409         # exceeds what is outstanding


def test_ignore_and_resuggest(env):
    env.import_csv()
    cid = env.credits()[12345.0]["id"]
    assert env.post(f"/v1/credits/{cid}/ignore").status_code == 200 and env.credits()[12345.0]["status"] == "ignored"
    big = env.credits()[100000.0]
    env.post(f"/v1/matches/{big['matches'][0]['id']}/confirm")
    assert env.post(f"/v1/credits/{big['id']}/ignore").status_code == 409


def test_learned_payer_alias_improves_the_next_match(env):
    env.import_csv()
    env.post(f"/v1/matches/{env.credits()[100000.0]['matches'][0]['id']}/confirm")
    r = env.post("/v1/credits/import", {"format": "rows", "rows": [{"date": "2027-01-20", "amount": 50000, "narration": "UPI/777777777777/ZENITH TOOLING SYSTEMS PVT LTD/x@y"}]}).json()
    c = [c for c in env.c.get("/v1/credits?status=suggested", headers=env.h).json()["credits"] if c["amount"] == 50000.0][0]
    assert c["matches"][0]["allocations"][0]["invoice_number"] == "INV/2" and "LEARNED_PAYER" in c["matches"][0]["reasons"]


def test_auto_confirm_only_for_a_single_high_confidence_suggestion(tmp_path):
    e = Env(tmp_path, auto_confirm_at=0.9)
    e.import_csv()
    cs = e.credits()
    assert cs[100000.0]["status"] == "matched" and cs[100000.0]["matches"][0]["confirmed_by"] == "auto"
    assert cs[73500.0]["status"] == "suggested"                                         # below 0.9: waits for a person
    assert e.inv("INV/1")["outstanding"] == 0.0


def test_buyer_page_shows_settled_after_a_confirmed_match(env):
    env.import_csv()
    iid = env.inv("INV/1")["id"]
    token = env.post(f"/v1/invoices/{iid}/link").json()["url"].split("/p/")[1]
    assert "Pay " in env.c.get(f"/p/{token}").text
    env.post(f"/v1/matches/{env.credits()[100000.0]['matches'][0]['id']}/confirm")
    body = env.c.get(f"/p/{token}").text
    assert "has been settled" in body and "Pay " not in body


def test_customers_are_isolated_and_everything_is_audited(env, tmp_path):
    env.import_csv()
    mid = env.credits()[100000.0]["matches"][0]["id"]
    other = Env(tmp_path / "other")
    assert other.post(f"/v1/matches/{mid}/confirm").status_code == 404 and other.credits() == {}
    env.post(f"/v1/matches/{mid}/confirm")
    actions = [a["action"] for a in env.store.audit_entries()]
    assert "credits.import" in actions and "match.confirm" in actions
