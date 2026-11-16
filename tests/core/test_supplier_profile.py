from tests.core.test_owner_ui import Env, FORM, env  # noqa: F401  (reuse the owner-UI environment)
import re
from datetime import date
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from dueclock.core.validate import clean_supplier, coverage_preview

TODAY = date(2027, 1, 20)
OK = dict(business_name=" Precision Metal Works ", category="micro", activity="Manufacturing", udyam_number="udyam-ka-03-0012345", udyam_registration_date="2024-01-10",
          gstin="29abcde1234f1z5", upi_vpa="precision@icici", phone="098450 12345", email="a@b.in")


def test_clean_supplier_normalises_a_good_profile():
    v, e = clean_supplier(OK, TODAY)
    assert e == {} and v["business_name"] == "Precision Metal Works" and v["category"] == "MICRO" and v["activity"] == "MANUFACTURING"
    assert v["udyam_number"] == "UDYAM-KA-03-0012345" and v["gstin"] == "29ABCDE1234F1Z5" and v["phone"] == "+919845012345" and v["udyam_registration_date"] == "2024-01-10"


@pytest.mark.parametrize("field,bad", [("business_name", ""), ("business_name", "x" * 121), ("category", "huge"), ("category", ""), ("activity", "farming"), ("udyam_number", "UDYAM-KA-3-1"),
                                       ("udyam_registration_date", "2028-01-01"), ("udyam_registration_date", "10/01/2024"), ("udyam_registration_date", "1999-01-01"),
                                       ("gstin", "29ABCDE1234F1Z"), ("upi_vpa", "not a vpa"), ("upi_vpa", "p@bank"), ("phone", "12345"), ("email", "nope")])
def test_each_invalid_field_is_reported_by_name(field, bad):
    _, e = clean_supplier(dict(OK, **{field: bad}), TODAY)
    assert field in e and len(e) == 1


def test_optional_fields_may_be_blank_and_api_mode_is_lenient_about_category():
    v, e = clean_supplier(dict(business_name="X", category="MICRO", activity="SERVICES"), TODAY)
    assert e == {} and v["udyam_number"] is None and v["upi_vpa"] is None and v["phone"] is None
    v, e = clean_supplier(dict(business_name="X"), TODAY, strict=False)
    assert e == {} and v["category"] == "UNKNOWN"
    assert "category" in clean_supplier(dict(business_name="X"), TODAY)[1]


def test_coverage_preview_explains_the_consequences():
    kinds = lambda v, inv=(): [k for k, _ in coverage_preview(v, list(inv))]
    assert kinds({"category": "MICRO", "activity": "MANUFACTURING", "udyam_registration_date": "2024-01-10", "upi_vpa": "a@b"}) == ["ok"]
    assert "bad" in kinds({"category": "MICRO", "activity": "TRADING", "upi_vpa": "a@b"}) and "bad" in kinds({"category": "LARGE", "activity": "SERVICES"})
    assert kinds({}) == ["warn", "warn"]
    text = " ".join(t for _, t in coverage_preview({"category": "MICRO", "activity": "SERVICES", "udyam_registration_date": "2026-05-01"}, [{"invoice_date": "2026-04-01"}, {"invoice_date": "2026-06-01"}]))
    assert "1 invoice(s) are dated before your registration date" in text and "No UPI ID" in text
    assert "registration date missing" in " ".join(t for _, t in coverage_preview({"category": "MICRO", "activity": "SERVICES"}, []))


def form(**kw):
    return urlencode(kw)


def test_api_rejects_invalid_profiles_with_field_messages(env):
    r = env.c.put("/v1/supplier", json={"business_name": "X", "category": "MICRO", "activity": "SERVICES", "gstin": "bad", "upi_vpa": "nope"}, headers=env.h)
    assert r.status_code == 422 and set(r.json()["detail"]) == {"gstin", "upi_vpa"}
    assert env.store.get_supplier()["business_name"] == "Precision Metal Works"                        # the previous profile is untouched
    assert env.c.put("/v1/supplier", json={"business_name": "Renamed", "category": "small", "activity": "services"}, headers=env.h).status_code == 200
    assert env.store.get_supplier()["category"] == "SMALL"


def test_settings_page_shows_the_form_with_current_values_and_preview(env):
    h = env.c.get("/app/settings").text
    assert 'action="/app/settings/business"' in h and 'value="Precision Metal Works"' in h and "Covered: the 15/45-day payment clock" in h
    assert '<option value="MICRO" selected>' in h and '<option value="MANUFACTURING" selected>' in h and "2024-01-10" in h


def test_saving_valid_details_updates_the_profile_audits_and_flashes(env):
    r = env.post("/app/settings/business", form(business_name="New Name Pvt Ltd", category="SMALL", activity="SERVICES", udyam_number="UDYAM-KA-03-0012345",
                                                 udyam_registration_date="2023-05-01", upi_vpa="newname@icici", gstin="", phone="", email=""), page="/app/settings")
    assert r.status_code == 303 and r.headers["location"] == "/app/settings?msg=saved"
    sup = env.store.get_supplier()
    assert sup["business_name"] == "New Name Pvt Ltd" and sup["category"] == "SMALL" and sup["udyam_number"] == "UDYAM-KA-03-0012345"
    assert any(a["action"] == "supplier.set" and a["actor"] == "owner screen" for a in env.store.audit_entries())
    assert "Saved." in env.c.get(r.headers["location"]).text


def test_invalid_input_rerenders_with_messages_keeps_typed_values_and_saves_nothing(env):
    before = env.store.get_supplier()
    r = env.post("/app/settings/business", form(business_name="Typed Name <b>", category="", activity="TRADING", udyam_number="BAD", udyam_registration_date="2099-01-01",
                                                 gstin="", upi_vpa="x y", phone="", email=""), page="/app/settings")
    assert r.status_code == 400
    t = r.text
    assert "Please fix the highlighted fields." in t and "Format is UDYAM-XX-00-0000000" in t and "cannot be in the future" in t and "Choose Micro, Small, Medium or Large" in t
    assert 'value="Typed Name &lt;b&gt;"' in t and "<b>" not in t.split("Your business")[1].split("</form>")[0].replace("<button", "")        # typed value kept, HTML escaped
    assert env.store.get_supplier()["business_name"] == before["business_name"] and env.store.get_supplier()["udyam_number"] == before["udyam_number"]


def test_business_form_needs_login_and_csrf(env, tmp_path):
    assert env.post("/app/settings/business", form(business_name="X", category="MICRO", activity="SERVICES"), csrf=False).status_code == 403
    other = TestClient(env.app, follow_redirects=False)
    r = other.post("/app/settings/business", content=form(business_name="X", category="MICRO", activity="SERVICES"), headers=FORM)
    assert r.status_code == 303 and r.headers["location"] == "/app/login"
    assert env.store.get_supplier()["business_name"] == "Precision Metal Works"


def test_dashboard_banner_until_the_profile_is_complete(tmp_path):
    e = Env(tmp_path)
    e.store.set_supplier({"business_name": "X", "category": "UNKNOWN", "activity": "UNKNOWN"})
    e.login()
    assert "Finish your business details" in e.c.get("/app").text
    e.post("/app/settings/business", form(business_name="X", category="MICRO", activity="SERVICES"), page="/app/settings")
    assert "Finish your business details" not in e.c.get("/app").text
    e2 = Env(tmp_path / "second"); e2.login()
    assert "Finish your business details" not in e2.c.get("/app").text                                    # complete profile: no banner


def test_changing_the_profile_changes_the_clock(env):
    before = env.inv("INV/1")["clock"]["covered"]
    env.post("/app/settings/business", form(business_name="X", category="MICRO", activity="TRADING"), page="/app/settings")
    assert before is True and env.inv("INV/1")["clock"]["covered"] is False                              # a trader is not covered
