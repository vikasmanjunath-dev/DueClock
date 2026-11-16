import json

import pytest

from dueclock.connector.config import ConfigError, ConnectorConfig
from dueclock.connector.tally_client import (ReadOnlyViolation, TallyClient, TallyError, assert_read_only, bills_receivable_request,
                                             companies_request, decode_response)
from fake_tally import FakeTally, bill, flat_xml, nested_xml

KEY = "dc_" + "x" * 40


def cfg(**kw):
    base = dict(cloud_url="https://api.example.com", api_key=KEY)
    base.update(kw)
    return ConnectorConfig(**base)


def test_config_valid_and_redacted(tmp_path, monkeypatch):
    assert cfg().validate().redacted()["api_key"] == KEY[:6] + "…"
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"cloud_url": "https://api.example.com", "tally_port": 9001, "companies": ["A Co"], "unknown_field": 1}))
    monkeypatch.setenv("DUECLOCK_API_KEY", KEY)
    loaded = ConnectorConfig.load(str(p))
    assert loaded.api_key == KEY and loaded.tally_port == 9001 and loaded.companies == ["A Co"]


@pytest.mark.parametrize("kw", [dict(cloud_url="http://api.example.com"), dict(cloud_url="not a url"), dict(api_key="short"), dict(api_key=""),
                                dict(tally_port=0), dict(schedule_hour=24), dict(batch_size=0), dict(batch_size=5001),
                                dict(mass_settlement_ratio=0), dict(companies=[" "])])
def test_config_rejects_bad_values(kw):
    with pytest.raises(ConfigError):
        cfg(**kw).validate()


def test_http_allowed_only_for_local():
    cfg(cloud_url="http://127.0.0.1:8100").validate()
    cfg(cloud_url="http://localhost:8100").validate()


# ---- read-only guard
def test_export_requests_pass_the_guard():
    assert_read_only(bills_receivable_request("My Co & Sons"))
    assert_read_only(companies_request())
    assert "My Co &amp; Sons" in bills_receivable_request("My Co & Sons")        # escaped, cannot inject markup


@pytest.mark.parametrize("xml", [
    "<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER></ENVELOPE>",
    "<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER><BODY><IMPORTDATA/></BODY></ENVELOPE>",
    "<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER><BODY><TALLYMESSAGE><LEDGER ACTION=\"Delete\"/></TALLYMESSAGE></BODY></ENVELOPE>",
    "<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST></HEADER><BODY><X ACTION=\"Alter\"/></BODY></ENVELOPE>",
    "<ENVELOPE><HEADER></HEADER></ENVELOPE>", "not xml at all"])
def test_anything_that_could_write_is_refused(xml):
    with pytest.raises(ReadOnlyViolation):
        assert_read_only(xml)


def test_client_never_sends_a_refused_request():
    with FakeTally(nested_xml([bill(1)])) as t:
        c = TallyClient(port=t.port)
        with pytest.raises(ReadOnlyViolation):
            c.post("<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER></ENVELOPE>")
        assert t.requests == []


def test_decode_utf16_and_control_characters():
    raw = ("﻿<R><A>x&#4;y</A></R>").encode("utf-16")
    assert decode_response(raw) == "<R><A>xy</A></R>"
    assert decode_response("<R>\x04ok</R>".encode("utf-8")) == "<R>ok</R>"


def test_probe_detects_product_and_down_server():
    with FakeTally(banner="TallyPrime Server is Running") as t:
        pr = TallyClient(port=t.port).probe()
        assert pr.running and pr.product == "TallyPrime"
    with FakeTally(banner="Tally.ERP 9 Server is Running") as t:
        assert TallyClient(port=t.port).probe().product == "Tally.ERP 9"
    with FakeTally() as t:
        port = t.port
    assert not TallyClient(port=port, timeout=1).probe().running           # server stopped


def test_fetch_bills_nested_and_flat_shapes_and_utf16():
    with FakeTally(nested_xml([bill(1, closing=40000.0, credit=45), bill(2)]), utf16=True) as t:
        r = TallyClient(port=t.port).fetch_bills("A Co")
        assert len(r.records) == 2 and r.records[0]["pending_amount"] == 40000.0 and r.records[0]["agreed_credit_days"] == 45
        assert "A Co" in t.requests[0]
    with FakeTally(flat_xml([dict(bill(3, closing=75000.0), due="20261016")])) as t:
        r = TallyClient(port=t.port).fetch_bills()
        rec = r.records[0]
        assert rec["pending_amount"] == 75000.0 and rec["amount_known"] is False and rec["agreed_credit_days"] == 15


def test_tally_http_error_raises():
    with FakeTally("", status=500) as t:
        with pytest.raises(TallyError):
            TallyClient(port=t.port).fetch_bills()


def test_list_companies():
    xml = "<ENVELOPE><COMPANY><NAME>Precision Metal Works</NAME></COMPANY><COMPANY><NAME>Second Co</NAME></COMPANY></ENVELOPE>"
    with FakeTally(xml) as t:
        assert TallyClient(port=t.port).list_companies() == ["Precision Metal Works", "Second Co"]
