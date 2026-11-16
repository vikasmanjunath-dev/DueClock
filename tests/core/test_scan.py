import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from dueclock.core import scan as S
from dueclock.core.api import create_core_app
from dueclock.core.scan import VendorStatus, run_scan

UTC = timezone.utc
AS_OF = date(2027, 1, 20)
CSV = """Vendor,Bill No,Bill Date,Amount,Pending,Category,Activity,Credit Days
Apex Castings,A1,2027-01-01,"1,00,000.00",,Micro,Manufacturing,
Apex Castings,A2,2027-02-01,50000,50000,Micro,Manufacturing,45
Beta Traders,B1,2027-01-01,30000,,Small,Wholesale Trader,
Gamma Services,G1,2026-12-01,20000,5000,Small Enterprise,Services,
Delta Unknown,D1,2027-01-02,80000,80000,,,
Delta Unknown,D2,2027-03-30,10000,10000,MSME,,
Epsilon Ltd,E1,2027-01-01,500000,500000,Large,Manufacturing,
Zeta,Z1,2027-01-01,1000,0,Micro,Manufacturing,
"""


def scan(text=CSV, as_of=AS_OF, **kw):
    recs, errs = S.parse_payables_csv(text)
    return run_scan(recs, as_of, errors=errs, **kw)


def by_bill(r):
    return {b["bill_no"]: b for b in r.bills}


# ---------------- engine
def test_statuses_amounts_and_summary():
    r = scan()
    b = by_bill(r)
    assert b["A1"]["status"] == S.PAST_LIMIT and b["A1"]["due_date"] == "2027-01-16" and b["A1"]["days"] == -4 and b["A1"]["interest"] == pytest.approx(191.67, abs=0.01)
    assert b["A2"]["status"] == S.CROSSES and b["A2"]["due_date"] == "2027-03-18"                      # 45-day written agreement
    assert b["B1"]["status"] == S.NOT_COVERED and b["E1"]["status"] == S.NOT_COVERED and b["Z1"]["status"] == S.SETTLED
    assert b["G1"]["status"] == S.PAST_LIMIT and b["G1"]["outstanding"] == 5000.0                        # part payment: amount minus nothing; pending column wins
    assert b["D1"]["status"] == S.UNVERIFIED and b["D1"]["would_be"] == S.PAST_LIMIT and b["D2"]["would_be"] == S.WITHIN
    s = r.summary
    assert s["past_limit"] == {"amount": 105000.0, "bills": 2} and s["crosses_by_year_end"] == {"amount": 50000.0, "bills": 1}
    assert s["confirmed_at_risk"] == 155000.0 and s["possible_at_risk"] == {"amount": 80000.0, "bills": 1}
    assert s["vendors_unverified"] == 1 and s["vendors_not_covered"] == 2 and s["bills"] == 8 and s["vendors"] == 6
    assert [p["bill_no"] for p in s["pay_first"]] == ["G1", "A1", "A2"]                                  # earliest limit first
    assert r.fy_end == date(2027, 3, 31) and r.fy_label == "FY 2026-27"


def test_unverified_vendors_never_count_as_confirmed():
    s = scan().summary
    assert s["confirmed_at_risk"] == 155000.0 and 80000.0 not in (s["past_limit"]["amount"], s["confirmed_at_risk"])


def test_default_terms_change_the_dates():
    r = scan(default_agreed_days=45)
    b = by_bill(r)
    assert b["A1"]["status"] == S.CROSSES and b["A1"]["due_date"] == "2027-02-15" and b["D1"]["would_be"] == S.CROSSES


def test_verification_hook_resolves_unknown_vendors():
    r = scan(status_lookup=lambda rec: VendorStatus("SMALL", "MANUFACTURING") if rec["vendor"].startswith("Delta") else None)
    assert by_bill(r)["D1"]["status"] == S.PAST_LIMIT and r.summary["confirmed_at_risk"] == 235000.0 and r.summary["vendors_unverified"] == 0


def test_year_end_rolls_over():
    r = scan(as_of=date(2027, 4, 1))
    assert r.fy_end == date(2028, 3, 31) and r.fy_label == "FY 2027-28"


def test_parsing_errors_and_aliases():
    assert S.parse_payables_csv("hello")[1][0].startswith("No header row")
    recs, errs = S.parse_payables_csv("Supplier Name,Invoice Date,Bill Amount,Amount Paid\nAcme,05/01/2027,\"10,000\",\"2,500\"\n,05/01/2027,1,0\nBad,notadate,5,0\n")
    assert len(recs) == 1 and recs[0]["pending"] == 7500.0 and recs[0]["bill_date"] == date(2027, 1, 5) and len(errs) == 2
    assert S.norm_category("Micro Enterprise") == "MICRO" and S.norm_category("MSME") == "UNKNOWN" and S.norm_activity("Wholesale Trader") == "TRADING" and S.norm_activity("IT services") == "SERVICES"
    big = "Vendor,Bill Date,Amount\n" + "\n".join(f"V{i},2027-01-01,10" for i in range(S.MAX_ROWS + 5))
    recs, errs = S.parse_payables_csv(big)
    assert len(recs) == S.MAX_ROWS and any("first 5000" in e for e in errs)


def test_tally_xml_payables():
    xml = "<ENVELOPE><BILLFIXED><BILLDATE>20270101</BILLDATE><BILLREF>X1</BILLREF><BILLPARTY>Apex</BILLPARTY></BILLFIXED><BILLCL>25,000.00</BILLCL><BILLDUE>20270116</BILLDUE></ENVELOPE>"
    recs, errs = S.parse_payables_xml(xml)
    assert len(recs) == 1 and recs[0]["vendor"] == "Apex" and recs[0]["pending"] == 25000.0 and errs == []


def test_csv_export_is_injection_safe():
    recs, _ = S.parse_payables_csv("Vendor,Bill Date,Amount,Category,Activity\n=HYPERLINK(\"http://x\"),2027-01-01,100,Micro,Manufacturing\n+cmd,2027-01-01,100,Micro,Manufacturing\n")
    out = S.to_csv(run_scan(recs, AS_OF))
    assert "'=HYPERLINK" in out and "'+cmd" in out and "not legal advice" in out and "\n=HYP" not in out


# ---------------- web flow
class Env:
    def __init__(self, tmp_path):
        self.now = datetime(2027, 1, 20, 6, 0, tzinfo=UTC)
        self.app = create_core_app(str(tmp_path), today_fn=lambda: self.now.date(), now_fn=lambda: self.now)
        self.c = TestClient(self.app, follow_redirects=False)

    def run(self, text=CSV, **extra):
        body = "format=csv&as_of=2027-01-20&default_days=0&ledger=" + quote(text) + "".join(f"&{k}={v}" for k, v in extra.items())
        return self.c.post("/exposure-scan", content=body, headers={"content-type": "application/x-www-form-urlencoded"})

    def sid(self, **kw):
        r = self.run(**kw)
        assert r.status_code == 303
        return r.headers["location"].rsplit("/", 1)[1]


@pytest.fixture()
def env(tmp_path):
    return Env(tmp_path)


def test_form_and_headline_are_free(env):
    form = env.c.get("/exposure-scan")
    assert form.status_code == 200 and "Free year-end exposure scan" in form.text and "<script" not in form.text.lower() and "default-src 'none'" in form.headers["content-security-policy"]
    sid = env.sid()
    page = env.c.get(f"/exposure-scan/r/{sid}").text
    assert "₹1,55,000.00" in page and "₹1,05,000.00" in page and "₹50,000.00" in page and "₹80,000.00" in page and "31 Mar 2027" in page
    assert "Apex Castings" not in page and "Unlock the full list" in page and "not legal advice" in page          # vendors stay hidden until email


def test_unlock_needs_a_valid_email_and_consent_then_shows_everything(env):
    sid = env.sid()
    post = lambda body: env.c.post(f"/exposure-scan/r/{sid}/unlock", content=body, headers={"content-type": "application/x-www-form-urlencoded"})
    for bad in ("email=nope&consent=on", "email=a%40b.co", "email=&consent=on"):
        r = post(bad)
        assert r.status_code == 400 and "valid email" in r.text
    assert env.c.get(f"/exposure-scan/r/{sid}/csv").status_code == 403
    assert env.app.state.control.db.query("SELECT * FROM scan_leads") == []
    ok = post("email=Finance%40Buyer.co&consent=on")
    assert ok.status_code == 303 and ok.headers["location"].endswith("?msg=unlocked")
    page = env.c.get(f"/exposure-scan/r/{sid}").text
    assert "Apex Castings" in page and "Pay these first" in page and "Download CSV" in page and "Unlock the full list" not in page
    lead = env.app.state.control.db.query("SELECT email, scan_id FROM scan_leads")
    assert lead == [{"email": "finance@buyer.co", "scan_id": sid}]
    csv_r = env.c.get(f"/exposure-scan/r/{sid}/csv")
    assert csv_r.status_code == 200 and csv_r.headers["content-type"].startswith("text/csv") and "attachment" in csv_r.headers["content-disposition"]
    assert csv_r.text.count("\n") >= 10 and "Past the limit now" in csv_r.text


def test_data_expires_and_can_be_deleted(env):
    sid = env.sid()
    assert env.c.post(f"/exposure-scan/r/{sid}/delete").headers["location"].endswith("?msg=deleted")
    assert env.c.get(f"/exposure-scan/r/{sid}").status_code == 404 and "deleted" in env.c.get("/exposure-scan?msg=deleted").text
    sid2 = env.sid()
    env.now += timedelta(hours=25)
    assert env.c.get(f"/exposure-scan/r/{sid2}").status_code == 404
    assert env.app.state.control.db.query("SELECT id FROM scans") == []                      # purged, not just hidden


def test_privacy_no_raw_ip_and_unguessable_ids(env):
    a, b = env.sid(), env.sid()
    assert a != b and len(a) >= 30
    row = env.app.state.control.db.query("SELECT ip_hash FROM scans")[0]
    assert row["ip_hash"] != "testclient" and len(row["ip_hash"]) == 24
    assert env.c.get("/exposure-scan/r/short").status_code == 404


def test_limits_and_bad_input(env):
    assert env.run("hello").status_code == 400 and "No header row" in env.run("hello").text
    assert env.c.post("/exposure-scan", content="as_of=garbage&ledger=" + quote(CSV), headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 400
    assert env.c.post("/exposure-scan", content="ledger=" + "x" * 1_300_000, headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 413
    for _ in range(10 - 0):
        env.sid()
    assert env.run().status_code == 429
    env.now += timedelta(hours=2)
    assert env.run().status_code == 303


def test_hostile_vendor_names_are_escaped(env):
    sid = env.sid(text="Vendor,Bill Date,Amount,Category,Activity\n<script>alert(1)</script>,2027-01-01,5000,Micro,Manufacturing\n")
    env.c.post(f"/exposure-scan/r/{sid}/unlock", content="email=a%40b.co&consent=on", headers={"content-type": "application/x-www-form-urlencoded"})
    page = env.c.get(f"/exposure-scan/r/{sid}").text
    assert "<script>alert" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


# ---------------- API
def test_api_scan_needs_a_key_and_returns_json(env):
    _, key = env.app.state.control.create_tenant("CA Firm")
    body = {"format": "csv", "text": CSV, "as_of": "2027-01-20"}
    assert env.c.post("/v1/scan", json=body).status_code == 401
    r = env.c.post("/v1/scan", json=body, headers={"X-API-Key": key}).json()
    assert r["summary"]["confirmed_at_risk"] == 155000.0 and r["fy_label"] == "FY 2026-27" and len(r["bills"]) == 8
    rows = env.c.post("/v1/scan", json={"format": "rows", "rows": [{"Vendor": "Z", "Bill Date": "2027-01-01", "Amount": "1000", "Category": "Micro", "Activity": "Services"}]}, headers={"X-API-Key": key}).json()
    assert rows["summary"]["confirmed_at_risk"] == 1000.0
    assert env.c.post("/v1/scan", json={"format": "pdf", "text": "x"}, headers={"X-API-Key": key}).status_code == 422
    assert env.c.post("/v1/scan", json={"format": "csv", "text": "junk"}, headers={"X-API-Key": key}).status_code == 422
