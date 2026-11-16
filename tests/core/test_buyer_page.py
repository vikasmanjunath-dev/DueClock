from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from dueclock.core.api import create_core_app
from dueclock.core.buyer_page import CSS_HASH, fmt_date, inr, upi_link, valid_vpa
from dueclock.core.cadence_plan import plan
from dueclock.core.clock import InvoiceFacts, SupplierFacts, evaluate
from dueclock.core.tokens import TokenSigner

NOW = datetime(2027, 1, 20, 10, 0, tzinfo=timezone.utc)
SUPPLIER = {"business_name": "Precision Metal Works", "category": "MICRO", "activity": "MANUFACTURING", "udyam_registration_date": "2024-01-10", "upi_vpa": "precision@icici"}
REC = [
    dict(guid="a", invoice_number="INV/1", invoice_date="2027-01-01", delivery_date="2027-01-01", buyer_name="Zenith Tooling", amount=100000, pending_amount=100000),
    dict(guid="b", invoice_number="INV/2", invoice_date="2027-01-02", buyer_name="Kaveri Hydraulics", amount=50000, pending_amount=0),
]


class Env:
    def __init__(self, tmp_path, supplier=SUPPLIER):
        self.clock = {"now": NOW}
        self.app = create_core_app(str(tmp_path), today_fn=lambda: self.clock["now"].date(), now_fn=lambda: self.clock["now"], public_base_url="http://buyer.test")
        self.c = TestClient(self.app, follow_redirects=False)
        self.tid, key = self.app.state.control.create_tenant("Precision")
        self.h = {"X-API-Key": key}
        put = self.c.put("/v1/supplier", json=supplier, headers=self.h) if supplier else None
        self.c.post("/v1/connector/sync", json={"connector_version": "0.1", "records": REC}, headers=self.h)
        self.ids = {i["invoice_number"]: i["id"] for i in self.c.get("/v1/invoices?include_paid=true", headers=self.h).json()["invoices"]}
        self.store = self.app.state.control.store_for(self.app.state.control.authenticate(key))
        if put is not None and put.status_code == 422:
            self.store.set_supplier(supplier)                    # deliberately invalid profile data (older records): the page must still render safely

    def link(self, number="INV/1", ttl=None):
        q = f"?ttl_days={ttl}" if ttl else ""
        url = self.c.post(f"/v1/invoices/{self.ids[number]}/link{q}", headers=self.h).json()["url"]
        return url.split("/p/")[1]

    def get(self, token, **kw):
        return self.c.get(f"/p/{token}", **kw)

    def status(self, number="INV/1"):
        return self.store.get_invoice(self.ids[number])["status"]


@pytest.fixture()
def env(tmp_path):
    return Env(tmp_path)


# ---------------- tokens
def test_token_roundtrip_tamper_expiry():
    s = TokenSigner(b"k" * 32)
    t = s.issue("tenant1", "link1", 2000)
    c = s.verify(t, 1000)
    assert (c.tenant_id, c.link_id, c.expires_at) == ("tenant1", "link1", 2000)
    assert s.verify(t, 2000) is None and s.verify(t, 3000) is None                 # expired
    assert s.verify(t[:-2] + ("AA" if not t.endswith("AA") else "BB"), 1000) is None   # bad signature
    head, body, sig = t.split(".")
    assert s.verify(f"{head}.{body[:-1]}x.{sig}", 1000) is None                    # tampered payload
    assert TokenSigner(b"z" * 32).verify(t, 1000) is None                           # other secret
    for junk in ("", "x", "a.b", "v1.a.b", "v2.a.b", "v1..", "💥"):
        assert s.verify(junk, 1000) is None
    with pytest.raises(ValueError):
        TokenSigner(b"short")


# ---------------- page content and safety
def test_page_shows_invoice_pay_link_and_tax_note(env):
    r = env.get(env.link())
    b = r.text
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    for s in ("INV/1", "₹1,00,000.00", "16 Jan 2027", "4 days overdue", "Precision Metal Works", "Pay ₹1,00,000.00 by UPI",
              "upi://pay?pa=precision%40icici", "am=100000.00", "cu=INR", "may not be deductible", "31 March 2027", "not legal advice",
              "I will pay on a date", "Check my exposure", "Privacy notice"):
        assert s in b, s
    assert "<script" not in b.lower() and 'lang="en"' in b and "viewport" in b
    assert len(r.content) < 15_000


def test_security_headers(env):
    h = env.get(env.link()).headers
    assert CSS_HASH in h["content-security-policy"] and "default-src 'none'" in h["content-security-policy"]
    assert h["cache-control"] == "no-store" and h["referrer-policy"] == "no-referrer" and "noindex" in h["x-robots-tag"]
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"


def test_html_is_escaped(tmp_path):
    e2 = Env(tmp_path, dict(SUPPLIER, business_name="<img src=x onerror=alert(1)>"))
    body = e2.get(e2.link()).text
    assert "<img src=x" not in body and "&lt;img src=x" in body


def test_invalid_tampered_revoked_and_expired_links_look_identical(env):
    token = env.link()
    bad = [env.get("not-a-token"), env.get(token[:-3] + "xyz"), env.get("v1.e30.AAAA")]
    assert all(r.status_code == 404 and "INV/1" not in r.text and "not valid" in r.text for r in bad)
    env.c.delete(f"/v1/invoices/{env.ids['INV/1']}/links", headers=env.h)
    assert env.get(token).status_code == 404                                         # revoked
    t2 = env.link(ttl=1)
    assert env.get(t2).status_code == 200
    env.clock["now"] += timedelta(days=2)
    assert env.get(t2).status_code == 404                                            # expired


def test_link_is_bound_to_one_invoice_and_one_customer(tmp_path):
    a, b = Env(tmp_path / "a"), Env(tmp_path / "b")
    ta, tb = a.link("INV/1"), b.link("INV/2")
    assert "INV/1" in a.get(ta).text and "INV/2" not in a.get(ta).text
    assert "settled" in b.get(tb).text
    assert b.get(ta).status_code == 404                                              # a's token on b's server: unknown tenant/secret
    assert a.c.post(f"/p/{ta}/promise", content="date=2027-01-25", headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 303


def test_paid_invoice_shows_settled_without_amounts(env):
    body = env.get(env.link("INV/2")).text
    assert "has been settled" in body and "₹" not in body and "Pay " not in body


def test_tax_note_only_for_covered_suppliers(tmp_path):
    for sup in (dict(SUPPLIER, activity="TRADING"), None, dict(SUPPLIER, udyam_registration_date="2028-01-01")):
        e2 = Env(tmp_path / str(sup), sup)
        assert "Your tax position" not in e2.get(e2.link()).text, sup


def test_no_pay_button_without_valid_vpa(tmp_path):
    for sup in (dict(SUPPLIER, upi_vpa=None), dict(SUPPLIER, upi_vpa="not a vpa")):
        e2 = Env(tmp_path / str(sup["upi_vpa"]), sup)
        b = e2.get(e2.link()).text
        assert "upi://" not in b and "ask Precision Metal Works for their payment details" in b


def test_long_names_stay_small(tmp_path):
    e2 = Env(tmp_path, dict(SUPPLIER, business_name="N" * 200))
    assert len(e2.get(e2.link()).content) < 20_000


# ---------------- promise and dispute
FORM = {"content-type": "application/x-www-form-urlencoded"}


def test_promise_flow_and_planner(env):
    t = env.link()
    r = env.c.post(f"/p/{t}/promise", content="date=2027-01-25", headers=FORM)
    assert r.status_code == 303 and r.headers["location"].endswith("?done=promise&lang=en")
    page = env.get(t, params={"done": "promise"}).text
    assert "We have noted the date" in page and "25 Jan 2027" in page
    row = env.store.get_invoice(env.ids["INV/1"])
    assert row["promised_date"] == "2027-01-25"
    assert any(e["kind"] == "promise" and e["payload"]["date"] == "2027-01-25" for e in env.store.events_for_invoice(env.ids["INV/1"]))
    res = evaluate(InvoiceFacts(date(2027, 1, 1), 100000.0, date(2027, 1, 1)), SupplierFacts("MICRO", "MANUFACTURING", date(2024, 1, 1)), date(2027, 1, 20))
    d = plan(res, [], opted_in=True, now=datetime(2027, 1, 20, 10), promised_date=date.fromisoformat(row["promised_date"]))
    assert (d.action, d.reason) == ("wait", "PROMISE_PENDING")


@pytest.mark.parametrize("body", ["date=2027-01-20", "date=2027-01-19", "date=2027-04-01", "date=garbage", "date=", "", "other=1"])
def test_bad_promise_dates_rejected(env, body):
    t = env.link()
    assert env.c.post(f"/p/{t}/promise", content=body, headers=FORM).status_code == 400
    assert env.store.get_invoice(env.ids["INV/1"])["promised_date"] is None


def test_dispute_flow_sync_safety_and_resolution(env):
    t = env.link()
    r = env.c.post(f"/p/{t}/dispute", content="reason=Wrong+amount&note=Short+by+5000", headers=FORM)
    assert r.status_code == 303 and env.status() == "DISPUTED"
    ev = [e for e in env.store.events_for_invoice(env.ids["INV/1"]) if e["kind"] == "dispute"][0]
    assert ev["payload"] == {"reason": "Wrong amount", "note": "Short by 5000"}
    res = evaluate(InvoiceFacts(date(2027, 1, 1), 100000.0, date(2027, 1, 1)), SupplierFacts("MICRO", "MANUFACTURING", date(2024, 1, 1)), date(2027, 1, 20))
    assert plan(res, [], opted_in=True, now=datetime(2027, 1, 20, 10), disputed=env.status() == "DISPUTED").reason == "DISPUTED"
    env.c.post("/v1/connector/sync", json={"connector_version": "0.1", "records": [dict(REC[0], pending_amount=100000)]}, headers=env.h)
    assert env.status() == "DISPUTED"                                                   # a Tally sync must not clear a dispute
    page = env.get(t).text
    assert "A dispute has been raised" in page and "I will pay on a date" not in page and 'action="/p/' not in page
    assert env.c.post(f"/p/{t}/dispute", content="reason=Other", headers=FORM).status_code == 303
    assert env.c.put(f"/v1/invoices/{env.ids['INV/1']}/dispute", json={"resolved": True}, headers=env.h).json() == {"resolved": True}
    assert env.status() == "PENDING"
    assert env.c.put(f"/v1/invoices/{env.ids['INV/1']}/dispute", json={"resolved": True}, headers=env.h).json() == {"resolved": False}


def test_dispute_needs_a_known_reason(env):
    t = env.link()
    assert env.c.post(f"/p/{t}/dispute", content="reason=because", headers=FORM).status_code == 400
    assert env.status() == "PENDING"


def test_rate_limit_on_buyer_posts(env):
    t = env.link()
    codes = [env.c.post(f"/p/{t}/promise", content=f"date=2027-01-{21 + i}", headers=FORM).status_code for i in range(6)]
    assert codes == [303] * 5 + [429]
    env.clock["now"] += timedelta(hours=2)
    assert env.c.post(f"/p/{t}/promise", content="date=2027-01-30", headers=FORM).status_code == 303


# ---------------- seller side
def test_views_are_counted_and_seller_endpoints_need_a_key(env):
    t = env.link()
    for _ in range(3):
        env.get(t)
    iid = env.ids["INV/1"]
    links = env.c.get(f"/v1/invoices/{iid}/links", headers=env.h).json()["links"]
    assert links[0]["view_count"] == 3 and links[0]["revoked"] == 0
    kinds = [e["kind"] for e in env.c.get(f"/v1/invoices/{iid}/events", headers=env.h).json()["events"]]
    assert kinds.count("view") == 3
    for path in (f"/v1/invoices/{iid}/links", f"/v1/invoices/{iid}/events"):
        assert env.c.get(path).status_code == 401
    assert env.c.post(f"/v1/invoices/{iid}/link").status_code == 401
    assert env.c.post("/v1/invoices/nope/link", headers=env.h).status_code == 404
    assert env.c.post(f"/v1/invoices/{iid}/link?ttl_days=0", headers=env.h).status_code == 422


# ---------------- helpers
def test_indian_number_and_date_formatting():
    assert inr(100000) == "₹1,00,000.00" and inr(999.5) == "₹999.50" and inr(12345678.9) == "₹1,23,45,678.90" and inr(0) == "₹0.00" and inr(1000) == "₹1,000.00"
    assert fmt_date(date(2027, 1, 5)) == "5 Jan 2027"


def test_upi_link_rules():
    ln = upi_link("shop@okhdfc", "A & B Metals", 1234.5, "Inv INV/9")
    assert ln.startswith("upi://pay?pa=shop%40okhdfc") and "pn=A%20%26%20B%20Metals" in ln and "am=1234.50" in ln and "tn=Inv%20INV%2F9" in ln
    assert upi_link("bad", "X", 10, "n") is None and upi_link("a@b", "X", 10, "n") is None and upi_link("shop@okhdfc", "X", 0, "n") is None
    assert valid_vpa("name.surname-1@oksbi") and not valid_vpa("no at sign") and not valid_vpa(None)
