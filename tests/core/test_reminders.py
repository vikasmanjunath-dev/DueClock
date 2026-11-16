import hashlib
import hmac
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from dueclock.core.api import create_core_app
from dueclock.core.messaging import CloudApiSender, MockSender, SendResult, normalize_mobile, sender_from_env
from dueclock.core.reminders import IST
from dueclock.core.store import TenantStore, Database
from dueclock.core.templates import TEMPLATES, build_vars, pick_language, step_key, template_name

UTC = timezone.utc
SUPPLIER = {"business_name": "Precision Metal Works", "category": "MICRO", "activity": "MANUFACTURING", "udyam_registration_date": "2024-01-10", "upi_vpa": "precision@icici"}


def rec(n, buyer="Zenith Tooling", mobile="+91 98450 12345", amount=100000, pending=None, date_="2027-01-01"):
    return dict(guid=f"g{n}", invoice_number=f"INV/{n}", invoice_date=date_, delivery_date=date_, buyer_name=buyer, buyer_mobile=mobile,
                amount=amount, pending_amount=amount if pending is None else pending)


def at(y, m, d, h=5, mi=0):          # 05:00 UTC = 10:30 IST
    return datetime(y, m, d, h, mi, tzinfo=UTC)


class Env:
    def __init__(self, tmp_path, sender=None, records=None, optin=True, **kw):
        self.now = at(2027, 1, 11)
        self.sender = MockSender() if sender is None else sender
        self.app = create_core_app(str(tmp_path), today_fn=lambda: self.now.astimezone(IST).date(), now_fn=lambda: self.now,
                                   public_base_url="http://buyer.test", sender=self.sender, wa_app_secret="appsecret", wa_verify_token="vt", **kw)
        self.c = TestClient(self.app, follow_redirects=False)
        self.tid, key = self.app.state.control.create_tenant("Precision")
        self.h = {"X-API-Key": key}
        self.c.put("/v1/supplier", json=SUPPLIER, headers=self.h)
        self.sync(records or [rec(1)])
        self.store = self.app.state.control.store_for(self.app.state.control.authenticate(key))
        if optin:
            self.optin_all()

    def sync(self, records):
        return self.c.post("/v1/connector/sync", json={"connector_version": "0.1", "records": records}, headers=self.h)

    def invoices(self):
        return {i["invoice_number"]: i for i in self.c.get("/v1/invoices?include_paid=true", headers=self.h).json()["invoices"]}

    def optin_all(self):
        for i in self.invoices().values():
            self.c.put(f"/v1/buyers/{i['buyer_id']}/opt-in", json={"source": "pilot_note"}, headers=self.h)

    def run(self, **kw):
        r = self.c.post("/v1/reminders/run", headers=self.h)
        assert r.status_code == 200, r.text
        return r.json()

    def events(self):
        return self.store.db.query("SELECT step, status, attempts, error, provider_message_id FROM reminder_events WHERE tenant_id = ? ORDER BY created_at, step", (self.store.tid,))


@pytest.fixture()
def env(tmp_path):
    return Env(tmp_path)


# ---------------- basic send and idempotency
def test_first_reminder_payload_and_link(env):
    r = env.run()
    assert (r["status"], r["sent"]) == ("ok", 1)
    m = env.sender.sent[0]
    assert m["template"] == "dc_s1_heads_up_en" and m["to"] == "919845012345" and m["language"] == "en"
    assert m["vars"] == ["Zenith Tooling", "Precision Metal Works", "INV/1", "1,00,000.00", "16 Jan 2027"]
    page = env.c.get(f"/p/{m['button']}")
    assert page.status_code == 200 and "INV/1" in page.text                      # the button token opens the buyer page
    ev = env.events()[0]
    assert (ev["step"], ev["status"], ev["provider_message_id"]) == ("S1", "sent", "wamid.mock.1")


def test_running_twice_never_sends_twice(env):
    env.run(); again = env.run()
    assert again["sent"] == 0 and len(env.sender.sent) == 1 and len(env.events()) == 1


def test_full_cadence_over_time(env):
    steps = {}
    day = at(2027, 1, 10)
    while day < at(2027, 2, 20):
        env.now = day
        before = len(env.sender.sent)
        env.run()
        for m in env.sender.sent[before:]:
            steps[m["template"].split("_")[1].upper()] = day.date()
        day += timedelta(days=1)
    assert steps == {"S1": date(2027, 1, 11), "S2": date(2027, 1, 16), "S3": date(2027, 1, 19), "S4": date(2027, 1, 26), "S5": date(2027, 2, 15)}


def test_missed_steps_send_only_the_latest(tmp_path):
    e = Env(tmp_path)
    e.now = at(2027, 1, 28)                                                        # 12 days overdue, nothing sent before
    e.run()
    assert [m["template"] for m in e.sender.sent] == ["dc_s4_overdue_10_en"]


# ---------------- hours, consent, pauses
def test_quiet_hours_use_ist(env):
    for h, mi, expect in [(2, 0, 0), (13, 30, 0), (3, 30, 1)]:                     # 07:30 IST, 19:00 IST, 09:00 IST
        env.now = at(2027, 1, 11, h, mi)
        env.run()
        assert len(env.sender.sent) == expect, (h, mi)
    env.now = at(2027, 1, 12, 13, 29)                                              # 18:59 IST: S1 was sent, nothing new due
    assert env.run()["sent"] == 0


def test_requires_opt_in_and_respects_opt_out(tmp_path):
    e = Env(tmp_path, optin=False)
    assert e.run()["sent"] == 0
    e.optin_all()
    assert e.run()["sent"] == 1
    e.now = at(2027, 1, 16)
    bid = e.invoices()["INV/1"]["buyer_id"]
    assert e.c.delete(f"/v1/buyers/{bid}/opt-in", headers=e.h).status_code == 200
    assert e.run()["sent"] == 0 and len(e.sender.sent) == 1


def test_pause_switches(env):
    iid = env.invoices()["INV/1"]["id"]
    env.c.post(f"/v1/invoices/{iid}/pause", headers=env.h)
    assert env.run()["sent"] == 0
    env.c.post(f"/v1/invoices/{iid}/resume", headers=env.h)
    env.c.post("/v1/reminders/pause", headers=env.h)
    assert env.run()["status"] == "paused" and not env.sender.sent
    env.c.post("/v1/reminders/resume", headers=env.h)
    assert env.run()["sent"] == 1


def test_dispute_and_promise_stop_or_delay(env):
    iid = env.invoices()["INV/1"]["id"]
    token = env.c.post(f"/v1/invoices/{iid}/link", headers=env.h).json()["url"].split("/p/")[1]
    env.now = at(2027, 1, 16)
    env.c.post(f"/p/{token}/promise", content="date=2027-01-20", headers={"content-type": "application/x-www-form-urlencoded"})
    env.sender.sent.clear()
    r = env.run()
    assert r["confirmations"] == 1 and env.sender.sent[0]["template"] == "dc_s6_promise_confirmation_en"     # thanked, no reminder
    assert env.sender.sent[0]["vars"][4] == "20 Jan 2027" and env.sender.sent[0]["button"] is None
    env.now = at(2027, 1, 18); assert env.run()["sent"] == 0                                      # promise pending
    env.now = at(2027, 1, 20); env.run()
    assert env.sender.sent[-1]["template"] == "dc_p1_promise_followup_en" and env.sender.sent[-1]["vars"][4] == "20 Jan 2027"
    env.c.post(f"/p/{token}/dispute", content="reason=Other", headers={"content-type": "application/x-www-form-urlencoded"})
    n = len(env.sender.sent); env.now = at(2027, 1, 30); env.run()
    assert len(env.sender.sent) == n                                                              # disputed: silence


def test_one_message_per_buyer_per_day(tmp_path):
    e = Env(tmp_path, records=[rec(1), rec(2, amount=50000)])                      # same buyer, two invoices due the same day
    assert e.run()["sent"] == 1
    e.now = at(2027, 1, 11, 6)
    assert e.run()["sent"] == 0
    e.now = at(2027, 1, 12)
    assert e.run()["sent"] == 1


def test_invalid_mobile_is_skipped_without_an_event(tmp_path):
    e = Env(tmp_path, records=[rec(1, mobile="12345")])
    r = e.run()
    assert r["sent"] == 0 and r["skipped"] == [{"invoice": "INV/1", "reason": "NO_VALID_MOBILE"}] and e.events() == []


def test_language_falls_back_until_approved(tmp_path):
    e = Env(tmp_path)
    e.c.put(f"/v1/buyers/{e.invoices()['INV/1']['buyer_id']}/language", json={"language": "hi"}, headers=e.h)
    e.run()
    assert e.sender.sent[0]["template"].endswith("_en")
    e2 = Env(tmp_path / "b", approved_languages=("en", "hi"))
    e2.c.put(f"/v1/buyers/{e2.invoices()['INV/1']['buyer_id']}/language", json={"language": "hi"}, headers=e2.h)
    e2.run()
    assert e2.sender.sent[0]["template"] == "dc_s1_heads_up_hi" and e2.sender.sent[0]["language"] == "hi"
    assert e.c.put(f"/v1/buyers/x/language", json={"language": "fr"}, headers=e.h).status_code == 422


# ---------------- failure handling
def test_transient_failure_retries_with_backoff_then_succeeds(tmp_path):
    e = Env(tmp_path, sender=MockSender(script=[SendResult(False, error="HTTP 500", retryable=True)]))
    assert e.run()["retried"] == 1 and e.events()[0]["status"] == "retry"
    e.now += timedelta(minutes=2); assert e.run()["sent"] == 0                        # not yet due
    e.now += timedelta(minutes=10); r = e.run()
    assert r["sent"] == 1 and e.events()[0]["status"] == "sent" and e.events()[0]["attempts"] == 2 and len(e.sender.sent) == 2


def test_gives_up_after_max_attempts(tmp_path):
    e = Env(tmp_path, sender=MockSender(script=[SendResult(False, error="HTTP 503", retryable=True)] * 8))
    for _ in range(8):
        e.run(); e.now += timedelta(hours=5)
        if e.now.hour >= 13: e.now = e.now.replace(hour=5) + timedelta(days=1)
    ev = e.events()[0]
    assert ev["status"] == "failed" and ev["attempts"] == 5


def test_permanent_failure_is_not_retried(tmp_path):
    e = Env(tmp_path, sender=MockSender(script=[SendResult(False, error="code 131026: undeliverable")]))
    assert e.run()["failed"] == 1
    e.now += timedelta(days=1); e.run()
    assert len(e.sender.sent) == 1 and e.events()[0]["status"] == "failed"


def test_auth_failure_stops_the_run_and_retries_later(tmp_path):
    e = Env(tmp_path, records=[rec(1), rec(2, buyer="Kaveri", mobile="+919886054321")], sender=MockSender(script=[SendResult(False, error="HTTP 401", fatal=True)]))
    r = e.run()
    assert r["status"] == "auth_failed" and len(e.sender.sent) == 1                    # second buyer not attempted
    e.now += timedelta(hours=2); r2 = e.run()
    assert r2["status"] == "ok" and r2["sent"] >= 1


def test_unknown_outcome_is_never_resent(tmp_path):
    e = Env(tmp_path, sender=MockSender(script=[RuntimeError("connection reset after send")]))
    e.run(); e.now += timedelta(days=1); e.run()
    assert len(e.sender.sent) == 1 and e.events()[0]["status"] == "unknown"


def test_circuit_breaker_and_run_cap(tmp_path):
    recs = [rec(i, buyer=f"Buyer {i}", mobile=f"+9198450000{i:02d}") for i in range(1, 7)]
    e = Env(tmp_path, records=recs, sender=MockSender(script=[SendResult(False, error="code 131026")] * 6))
    r = e.run()
    assert r["status"] == "circuit_open" and len(e.sender.sent) == 3
    e2 = Env(tmp_path / "cap", records=recs, max_sends_per_run=2)
    r2 = e2.run()
    assert r2["sent"] == 2 and sum(1 for w in r2["waiting"] if w["reason"] == "RUN_CAP") == 4


def test_dry_run_sends_and_records_nothing(env):
    p = env.c.get("/v1/reminders/preview", headers=env.h).json()
    assert p["decisions"] == [{"invoice": "INV/1", "step": "S1", "buyer": "Zenith Tooling"}] and not env.sender.sent and env.events() == []


def test_no_sender_configured_returns_503(tmp_path, monkeypatch):
    monkeypatch.delenv("DUECLOCK_WA_MODE", raising=False)
    app = create_core_app(str(tmp_path), now_fn=lambda: at(2027, 1, 11))
    _, key = app.state.control.create_tenant("X")
    c = TestClient(app)
    assert c.post("/v1/reminders/run", headers={"X-API-Key": key}).status_code == 503
    assert c.get("/v1/reminders/preview", headers={"X-API-Key": key}).status_code == 200


# ---------------- receipts
def test_payment_receipt_only_if_we_had_reminded(tmp_path):
    e = Env(tmp_path, records=[rec(1), rec(2, buyer="Kaveri", mobile="+919886054321", date_="2027-02-01")])
    e.run()                                                                           # S1 only for INV/1 (INV/2 not yet due)
    e.sync([rec(1, pending=0), rec(2, buyer="Kaveri", mobile="+919886054321", pending=0, date_="2027-02-01")])
    e.now = at(2027, 1, 12)
    r = e.run()
    assert r["receipts"] == 1
    last = e.sender.sent[-1]
    assert last["template"] == "dc_s7_payment_received_en" and last["vars"] == ["Zenith Tooling", "Precision Metal Works", "INV/1", "1,00,000.00"]
    e.now = at(2027, 1, 13); assert e.run()["receipts"] == 0


# ---------------- webhook and stats
def sig(body: bytes, secret="appsecret"):
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), "content-type": "application/json"}


def status_payload(mid, status, errors=None):
    st = {"id": mid, "status": status}
    if errors: st["errors"] = errors
    return json.dumps({"entry": [{"changes": [{"value": {"statuses": [st]}}]}]}).encode()


def test_webhook_updates_statuses_and_open_rate(tmp_path):
    e = Env(tmp_path, records=[rec(1), rec(2, buyer="Kaveri", mobile="+919886054321")])
    e.run()
    mids = [x["provider_message_id"] for x in e.events()]
    for mid, st in [(mids[0], "delivered"), (mids[0], "read"), (mids[0], "delivered"), (mids[1], "delivered")]:
        b = status_payload(mid, st)
        assert e.c.post("/v1/webhooks/whatsapp", content=b, headers=sig(b)).status_code == 200
    states = {x["provider_message_id"]: x["status"] for x in e.events()}
    assert states[mids[0]] == "read" and states[mids[1]] == "delivered"                # no downgrade
    assert e.c.get("/v1/reminders/stats", headers=e.h).json()["open_rate"] == 0.5
    b = status_payload(mids[1], "failed", [{"code": 131026, "title": "Message undeliverable"}])
    e.c.post("/v1/webhooks/whatsapp", content=b, headers=sig(b))
    assert [x for x in e.events() if x["provider_message_id"] == mids[1]][0]["status"] == "failed"


def test_webhook_rejects_bad_signatures_and_handshake(env):
    b = status_payload("wamid.x", "read")
    assert env.c.post("/v1/webhooks/whatsapp", content=b, headers=sig(b, "wrong")).status_code == 403
    assert env.c.post("/v1/webhooks/whatsapp", content=b, headers={"content-type": "application/json"}).status_code == 403
    assert env.c.post("/v1/webhooks/whatsapp", content=b, headers=sig(b)).json() == {"updated": 0}      # unknown message ignored
    ok = env.c.get("/v1/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    assert env.c.get("/v1/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "1"}).status_code == 403


def test_consent_and_reminder_endpoints_need_a_key_and_isolate_customers(tmp_path):
    a = Env(tmp_path / "a"); b = Env(tmp_path / "b")
    bid = a.invoices()["INV/1"]["buyer_id"]
    for method, path in [("put", f"/v1/buyers/{bid}/opt-in"), ("delete", f"/v1/buyers/{bid}/opt-in"), ("post", "/v1/reminders/run"), ("get", "/v1/reminders/stats"), ("post", "/v1/reminders/pause")]:
        assert getattr(a.c, method)(path).status_code in (401, 422)
    assert b.c.put(f"/v1/buyers/{bid}/opt-in", json={"source": "pilot_note"}, headers=b.h).status_code == 404
    assert b.c.put(f"/v1/buyers/{b.invoices()['INV/1']['buyer_id']}/opt-in", json={"source": "x"}, headers=b.h).status_code == 422
    a.run()
    assert a.c.get("/v1/reminders/stats", headers=a.h).json()["accepted"] == 1 and b.c.get("/v1/reminders/stats", headers=b.h).json()["accepted"] == 0


# ---------------- provider adapter
def cloud(handler):
    return CloudApiSender("PNID", "TOKEN", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_cloud_sender_payload_and_success():
    seen = {}

    def h(req):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})
    r = cloud(h).send_template(to="919845012345", template="dc_s1_heads_up_en", language="en", body_vars=["A", "B", "C", "D", "E"], button_suffix="tok")
    assert r.ok and r.provider_id == "wamid.ABC"
    assert seen["url"] == "https://graph.facebook.com/v21.0/PNID/messages" and seen["auth"] == "Bearer TOKEN"
    t = seen["body"]["template"]
    assert seen["body"]["to"] == "919845012345" and t["name"] == "dc_s1_heads_up_en" and t["language"] == {"code": "en"}
    assert [p["text"] for p in t["components"][0]["parameters"]] == ["A", "B", "C", "D", "E"]
    assert t["components"][1] == {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": "tok"}]}


@pytest.mark.parametrize("status,body,retryable,fatal", [
    (401, {"error": {"code": 190, "message": "expired"}}, False, True), (403, {}, False, True), (429, {}, True, False),
    (500, {}, True, False), (400, {"error": {"code": 130429, "message": "rate"}}, True, False),
    (400, {"error": {"code": 131026, "message": "undeliverable"}}, False, False), (400, {"error": {"code": 132001, "message": "template missing"}}, False, False)])
def test_cloud_sender_error_mapping(status, body, retryable, fatal):
    r = cloud(lambda req: httpx.Response(status, json=body)).send_template(to="919845012345", template="t", language="en", body_vars=["x"], button_suffix=None)
    assert not r.ok and r.retryable == retryable and r.fatal == fatal and r.error


def test_cloud_sender_network_error_is_retryable():
    def boom(req): raise httpx.ConnectError("down")
    r = cloud(boom).send_template(to="919845012345", template="t", language="en", body_vars=[], button_suffix=None)
    assert not r.ok and r.retryable


def test_sender_from_env(monkeypatch):
    monkeypatch.delenv("DUECLOCK_WA_MODE", raising=False)
    assert sender_from_env() is None
    monkeypatch.setenv("DUECLOCK_WA_MODE", "cloud")
    assert sender_from_env() is None                                                    # missing credentials
    monkeypatch.setenv("DUECLOCK_WA_PHONE_NUMBER_ID", "123"); monkeypatch.setenv("DUECLOCK_WA_TOKEN", "t")
    assert isinstance(sender_from_env(), CloudApiSender)


# ---------------- helpers
def test_normalize_mobile():
    assert normalize_mobile("+91 98450 12345") == "919845012345" and normalize_mobile("9845012345") == "919845012345"
    assert normalize_mobile("09845012345") == "919845012345" and normalize_mobile("919845012345") == "919845012345"
    for bad in (None, "", "12345", "5845012345", "+1 415 555 0100", "98450123456789"):
        assert normalize_mobile(bad) is None


def test_template_registry():
    assert step_key("S6:2027-01-25") == "S6" and template_name("S6:2027-01-25", "kn") == "dc_s6_promise_confirmation_kn"
    assert build_vars("S7", buyer="B", seller="S", invoice="I1", amount=1234.5, when=date(2027, 1, 1)) == ["B", "S", "I1", "1,234.50"]
    assert build_vars("S2", buyer=" B ", seller="S", invoice="I", amount=5, when=date(2027, 1, 1))[4] == "1 Jan 2027"
    assert pick_language("kn", ("en",)) == "en" and pick_language("kn", ("en", "kn")) == "kn" and pick_language(None, ("en",)) == "en"
    assert all(t.vars[:2] == ("buyer", "seller") for t in TEMPLATES.values())


def test_old_databases_are_migrated(tmp_path):
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE buyers (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, name_key TEXT NOT NULL, gstin TEXT, pan TEXT, mobile TEXT, email TEXT, has_written_agreement INTEGER NOT NULL DEFAULT 0, opt_in_at TEXT, created_at TEXT)")
    con.commit(); con.close()
    st = TenantStore(Database("sqlite:///" + path), "t1")
    cols = {r["name"] for r in st.db.query("PRAGMA table_info(buyers)")}
    assert {"language", "opt_in_source", "opted_out_at"} <= cols
    TenantStore(Database("sqlite:///" + path), "t1")                                  # idempotent
