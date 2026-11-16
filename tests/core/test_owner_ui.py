import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from dueclock.core.api import create_core_app
from dueclock.core.messaging import MockSender
from dueclock.core.reminders import IST

UTC = timezone.utc
SUP = {"business_name": "Precision Metal Works", "category": "MICRO", "activity": "MANUFACTURING", "udyam_registration_date": "2024-01-10", "upi_vpa": "precision@icici"}
CSV = "Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt\n18/01/27,UPI/412345678901/ZENITH TOOLING SYSTEMS/zenith@icici/INV/1,412345678901,,\"1,00,000.00\"\n"
FORM = {"content-type": "application/x-www-form-urlencoded"}


def rec(n, buyer, amount, d, mobile="+91 98450 12345"):
    return dict(guid=f"g{n}", invoice_number=f"INV/{n}", invoice_date=d, delivery_date=d, buyer_name=buyer, buyer_mobile=mobile, amount=amount, pending_amount=amount)


RECS = [rec(1, "Zenith Tooling Systems Pvt Ltd", 100000, "2027-01-01"), rec(2, "Zenith Tooling Systems Pvt Ltd", 50000, "2027-01-05"), rec(3, "Kaveri Hydraulics LLP", 75000, "2027-01-02", "+919886054321")]


class Env:
    def __init__(self, tmp_path, records=None):
        self.now = datetime(2027, 1, 11, 5, 0, tzinfo=UTC)
        self.sender = MockSender()
        self.app = create_core_app(str(tmp_path), today_fn=lambda: self.now.astimezone(IST).date(), now_fn=lambda: self.now, public_base_url="http://b.test", sender=self.sender)
        self.c = TestClient(self.app, follow_redirects=False)
        self.tid, self.key = self.app.state.control.create_tenant("Precision Metal Works")
        self.h = {"X-API-Key": self.key}
        self.c.put("/v1/supplier", json=SUP, headers=self.h)
        self.c.post("/v1/connector/sync", json={"connector_version": "0.1", "tally_version": "TallyPrime", "records": records or RECS}, headers=self.h)
        self.store = self.app.state.control.store_for(self.app.state.control.authenticate(self.key))

    def login(self, key=None):
        return self.c.post("/app/login", content=f"key={key or self.key}", headers=FORM)

    def csrf(self, path="/app"):
        return re.search(r'name="csrf" value="([0-9a-f]+)"', self.c.get(path).text).group(1)

    def post(self, path, body="", csrf=True, page="/app"):
        token = self.csrf(page) if csrf else ""
        return self.c.post(path, content=(f"csrf={token}&" if csrf else "") + body, headers=FORM)

    def inv(self, n):
        return {i["invoice_number"]: i for i in self.c.get("/v1/invoices?include_paid=true", headers=self.h).json()["invoices"]}[n]


@pytest.fixture()
def env(tmp_path):
    e = Env(tmp_path)
    assert e.login().status_code == 303
    return e


# ---------------- sign in
def test_login_required_wrong_key_rate_limit_and_cookie_flags(tmp_path):
    e = Env(tmp_path)
    for path in ("/app", "/app/payments", "/app/settings", f"/app/invoices/{e.inv('INV/1')['id']}"):
        r = e.c.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/app/login"
    assert e.c.post("/app/payments/x", content="").status_code in (404, 405)
    bad = e.login("dc_wrong")
    assert bad.status_code == 401 and "not recognised" in bad.text and "set-cookie" not in bad.headers
    ok = e.login()
    cookie = ok.headers["set-cookie"].lower()
    assert ok.status_code == 303 and "httponly" in cookie and "samesite=strict" in cookie and "path=/app" in cookie
    e2 = Env(tmp_path / "x")
    codes = [e2.login("dc_wrong").status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    assert e2.login().status_code == 429                                                  # even the right key is blocked during the lockout


def test_logout_tampered_and_expired_sessions(env):
    assert env.c.get("/app").status_code == 200
    env.c.cookies.set("dc_session", "garbage", path="/app")
    assert env.c.get("/app").status_code == 303
    env.c.cookies.clear()
    env.login()
    env.now += timedelta(hours=13)
    assert env.c.get("/app").status_code == 303                                           # expired
    env.now -= timedelta(hours=13)
    env.c.cookies.clear()
    env.login()
    assert env.post("/app/logout").status_code == 303
    assert env.c.get("/app").status_code == 303


# ---------------- pages
def test_dashboard_numbers_flags_and_filters(env):
    body = env.c.get("/app").text
    assert "₹2,25,000.00" in body and "Zenith Tooling" in body and "No consent" in body
    assert "<script" not in body.lower() and "Heads-up: due now" in body                  # INV/1 is 5 days from due: step S1 is due now
    env.now = datetime(2027, 1, 20, 5, 0, tzinfo=UTC)
    env.c.cookies.clear()
    env.login()
    page = env.c.get("/app").text
    assert "4 days overdue" in page and "43B(h) exposure" in page and "Overdue (2)" in page
    only_overdue = env.c.get("/app?filter=overdue").text
    assert "INV/1" in only_overdue and only_overdue.count("<tr>") == 3
    assert "Nothing here" in env.c.get("/app?filter=disputed").text
    h = env.c.get("/app").headers
    assert "default-src 'none'" in h["content-security-policy"] and h["cache-control"] == "no-store" and h["x-frame-options"] == "DENY"


def test_stale_sync_and_missing_sync_warnings(env, tmp_path):
    env.now += timedelta(hours=40)
    env.c.cookies.clear()
    env.login()
    assert "may be out of date" in env.c.get("/app").text
    fresh = Env(tmp_path / "nosync", records=[])
    fresh.c.post("/v1/connector/sync", json={"connector_version": "0", "records": []}, headers=fresh.h)
    fresh.login()
    assert "Collections" in fresh.c.get("/app").text


def test_invoice_page_shows_clock_working_and_404s(env):
    iid = env.inv("INV/1")["id"]
    body = env.c.get(f"/app/invoices/{iid}").text
    assert "Invoice INV/1" in body and "15 days" in body and "no written agreement" in body and "₹1,00,000.00" in body
    assert 'name="csrf"' in body and "<script" not in body.lower()
    assert env.c.get("/app/invoices/nope").status_code == 404


def test_one_customer_cannot_open_anothers_invoice(env, tmp_path):
    other = Env(tmp_path / "other")
    other.login()
    assert other.c.get(f"/app/invoices/{env.inv('INV/1')['id']}").status_code == 404


def test_hostile_names_are_escaped(tmp_path):
    e = Env(tmp_path, records=[rec(1, "<script>alert(1)</script>", 1000, "2027-01-01")])
    e.login()
    iid = e.inv("INV/1")["id"]
    for path in ("/app", f"/app/invoices/{iid}"):
        b = e.c.get(path).text
        assert "<script>alert" not in b and "&lt;script&gt;alert(1)&lt;/script&gt;" in b


# ---------------- invoice actions
def test_csrf_is_enforced(env):
    iid = env.inv("INV/1")["id"]
    r = env.post(f"/app/invoices/{iid}/pause", "value=yes", csrf=False)
    assert r.status_code == 403 and env.store.get_invoice(iid)["paused"] == 0
    r = env.c.post(f"/app/invoices/{iid}/pause", content="csrf=bad&value=yes", headers=FORM)
    assert r.status_code == 403 and env.store.get_invoice(iid)["paused"] == 0
    assert env.c.post("/app/settings/reminders", content="value=pause", headers=FORM).status_code == 403


def test_agreement_consent_and_pause_actions(env):
    iid = env.inv("INV/1")["id"]
    page = f"/app/invoices/{iid}"
    assert env.inv("INV/1")["clock"]["effective_days"] == 15
    r = env.post(f"{page}/agreement", "value=yes&days=45", page=page)
    assert r.status_code == 303 and "msg=agreement" in r.headers["location"] and env.inv("INV/1")["clock"]["effective_days"] == 45
    no = env.post(f"{page}/optin", "opt=yes", page=page)
    assert "err=consent" in no.headers["location"] and not env.store.get_invoice(iid)["opt_in_at"]
    ok = env.post(f"{page}/optin", "opt=yes&confirm=on", page=page)
    assert "msg=optin" in ok.headers["location"] and env.store.get_invoice(iid)["opt_in_at"]
    assert "Withdraw consent" in env.c.get(page).text
    env.post(f"{page}/optin", "opt=no", page=page)
    assert env.store.get_invoice(iid)["opted_out_at"]
    env.post(f"{page}/pause", "value=yes", page=page)
    assert env.store.get_invoice(iid)["paused"] == 1 and "Paused" in env.c.get("/app").text
    env.post(f"{page}/pause", "value=no", page=page)
    assert env.store.get_invoice(iid)["paused"] == 0


def test_buyer_link_create_show_revoke_and_dispute_resolution(env):
    iid = env.inv("INV/1")["id"]
    page = f"/app/invoices/{iid}"
    env.post(f"{page}/link", page=page)
    body = env.c.get(page).text
    url = re.search(r"<code>(http://b\.test/p/[^<]+)</code>", body).group(1)
    token = url.split("/p/")[1]
    assert env.c.get(f"/p/{token}").status_code == 200
    env.c.post(f"/p/{token}/dispute", content="reason=Wrong+amount", headers=FORM)
    assert "Mark dispute resolved" in env.c.get(page).text and "Disputed" in env.c.get("/app?filter=disputed").text
    env.post(f"{page}/dispute/resolve", page=page)
    assert env.store.get_invoice(iid)["status"] == "PENDING" and "Buyer raised a dispute" in env.c.get(page).text
    env.post(f"{page}/link/revoke", page=page)
    assert env.c.get(f"/p/{token}").status_code == 404 and "Buyer link (expires" not in env.c.get(page).text


# ---------------- payments
def test_payments_import_confirm_undo_and_manual_guards(env):
    r = env.post("/app/credits/import", "text=" + CSV.replace("\n", "%0A").replace(",", "%2C").replace(" ", "+").replace('"', "%22").replace("/", "%2F"), page="/app/payments")
    assert r.status_code == 303 and "msg=imported" in r.headers["location"]
    body = env.c.get("/app/payments?msg=imported").text
    assert "Statement imported" in body and "₹1,00,000.00" in body and "Confirm" in body and "INV/1" in body
    mid = re.search(r"/app/matches/([0-9a-f]+)/confirm", body).group(1)
    assert "msg=confirmed" in env.post(f"/app/matches/{mid}/confirm", page="/app/payments").headers["location"]
    assert env.inv("INV/1")["outstanding"] == 0.0 and "Undo" in env.c.get("/app/payments").text
    assert env.post(f"/app/matches/{mid}/confirm", page="/app/payments").headers["location"].endswith("err=conflict")
    assert "msg=undone" in env.post(f"/app/matches/{mid}/unconfirm", page="/app/payments").headers["location"]
    assert env.inv("INV/1")["outstanding"] == 100000.0
    assert env.post(f"/app/matches/{mid}/explode", page="/app/payments").status_code == 404
    cid = re.search(r"/app/credits/([0-9a-f]+)/manual", env.c.get("/app/payments").text).group(1)
    iid = env.inv("INV/2")["id"]
    assert env.post(f"/app/credits/{cid}/manual", f"invoice_id={iid}&amount=abc", page="/app/payments").headers["location"].endswith("err=invalid")
    assert env.post(f"/app/credits/{cid}/manual", f"invoice_id={iid}&amount=999999", page="/app/payments").headers["location"].endswith("err=conflict")
    assert "msg=manual" in env.post(f"/app/credits/{cid}/manual", f"invoice_id={iid}&amount=50%2C000", page="/app/payments").headers["location"]
    assert env.inv("INV/2")["outstanding"] == 0.0
    assert "err=conflict" in env.post(f"/app/credits/{cid}/ignore", page="/app/payments").headers["location"]     # a credit with confirmed money cannot be ignored


# ---------------- settings
def test_reminder_pause_and_preview(env):
    iid = env.inv("INV/1")["id"]
    env.post(f"/app/invoices/{iid}/optin", "opt=yes&confirm=on", page=f"/app/invoices/{iid}")
    s = env.c.get("/app/settings").text
    assert "Would send now" in s and "INV/1" in s and "Heads-up" in s and "WhatsApp sender: configured" in s
    assert "msg=paused" in env.post("/app/settings/reminders", "value=pause", page="/app/settings").headers["location"]
    assert "PAUSED" in env.c.get("/app/settings").text and "Reminders are paused" in env.c.get("/app").text
    assert env.c.post("/v1/reminders/run", headers=env.h).json()["status"] == "paused"
    env.post("/app/settings/reminders", "value=resume", page="/app/settings")
    assert "PAUSED" not in env.c.get("/app/settings").text and "TallyPrime" in env.c.get("/app/settings").text


def test_dashboard_scales(tmp_path):
    recs = [rec(i, f"Buyer {i % 40}", 1000 + i, "2027-01-01") for i in range(1, 301)]
    e = Env(tmp_path, records=recs)
    e.login()
    r = e.c.get("/app")
    assert r.status_code == 200 and len(r.content) < 250_000 and "Showing the first 200 of 300" in r.text
