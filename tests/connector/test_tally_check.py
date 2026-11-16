import json
import os
import re

import pytest

from dueclock.connector import cli
from dueclock.connector.config import ConfigError, ConnectorConfig
from dueclock.connector.tally_check import redact_xml, render_text, run_check, save_profile
from dueclock.connector.tally_client import TallyClient
from tests.connector.fake_tally import FakeTally, bill, flat_xml

BILLS = [bill(1, "Zenith Tooling", 100000.0, date="20261001"), bill(2, "Acme Fasteners Pvt Ltd", 50000.0, date="20260901"), bill(3, "Zenith Tooling", 25000.0, date="20260915")]
COMPANIES = "<ENVELOPE><BODY><DATA><COLLECTION><COMPANY><NAME>Shree Engineering Works</NAME></COMPANY></COLLECTION></DATA></BODY></ENVELOPE>"
KEY = "dc_" + "k" * 30


def responder(bills_ok=("data_explode", "data_plain"), companies_ok=("collection",), bills_body=None):
    def fn(raw):
        explode = "<EXPLODEFLAG>" in raw
        if "Bills Receivable" in raw:
            variant = "data_explode" if explode else "data_plain"
            return (200, bills_body if bills_body is not None else flat_xml(BILLS)) if variant in bills_ok else (500, "error")
        if "List of Companies" in raw and "<TYPE>Collection</TYPE>" in raw:
            return (200, COMPANIES) if "collection" in companies_ok else (200, "<ENVELOPE/>")
        if "List of Companies" in raw:
            return (200, "<ENVELOPE><BODY>no such report</BODY></ENVELOPE>")
        return (200, "<ENVELOPE/>")
    return fn


def check(srv, **kw):
    return run_check(TallyClient("127.0.0.1", srv.port, 5.0), **kw)


def test_redaction_keeps_numbers_dates_structure_and_hides_names():
    raw = flat_xml(BILLS)
    red = redact_xml(raw)
    for secret in ("Zenith", "Acme", "INV/1", "INV/2"):
        assert secret not in red
    assert "<BILLDATE>20261001</BILLDATE>" in red and "<BILLCL>100,000.00</BILLCL>" in red
    assert red.count("PARTY-1") == 2 and "PARTY-2" in red and "REF-3" in red          # same name -> same pseudonym
    assert redact_xml("<A x='secret'><BILLPARTY>Real Name</BILLPARTY></A>") == '<A x="x"><BILLPARTY>PARTY-1</BILLPARTY></A>'


def test_redaction_of_invalid_xml_still_hides_text():
    out = redact_xml("<ENVELOPE><BILLPARTY>Real & Co</BILLPARTY><BILLCL>10.00</BILLCL><BROKEN")
    assert "Real" not in out and "<BILLCL>10.00</BILLCL>" in out


def test_full_pass_chooses_variants_and_writes_redacted_captures(tmp_path):
    with FakeTally() as t:
        t.responder = responder()
        rep = check(t, out_dir=str(tmp_path))
    assert rep["verdict"] == "PASS" and rep["bills"]["chosen"] == "data_explode" and rep["companies"]["chosen"] == "collection"
    assert rep["profile"] == {"companies": "collection", "bills": "data_explode"}
    st = next(a for a in rep["bills"]["attempts"] if a["variant"] == "data_explode")["stats"]
    assert st["records"] == 3 and st["buyers"] == 2 and st["total_pending"] == 175000.0 and st["amount_not_in_report"] == 3
    assert any(f["code"] == "BILLS_PARSED" for f in rep["findings"])
    assert os.path.exists(tmp_path / "tally_check_report.json") and rep["captures"]
    blob = "".join(open(c, encoding="utf-8").read() for c in rep["captures"]) + (tmp_path / "tally_check_report.json").read_text()
    assert "Zenith" not in blob and "Acme" not in blob and "Shree Engineering" not in blob
    text = render_text(rep)
    assert text.startswith("Tally check: PASS") and "BILLS_PARSED" in text and "Zenith" not in text


def test_falls_back_to_second_bills_variant(tmp_path):
    with FakeTally() as t:
        t.responder = responder(bills_ok=("data_plain",))
        rep = check(t)
        assert rep["bills"]["chosen"] == "data_plain" and rep["profile"]["bills"] == "data_plain"
        first = next(a for a in rep["bills"]["attempts"] if a["variant"] == "data_explode")
        assert first["ok"] is False and "500" in first["error"]
        # a client using the saved profile sends the plain request
        t.requests.clear()
        c = TallyClient("127.0.0.1", t.port, 5.0, profile=rep["profile"])
        assert len(c.fetch_bills().records) == 3 and "<EXPLODEFLAG>" not in t.requests[-1]


def test_unreachable_tally_is_a_clear_fail():
    rep = run_check(TallyClient("127.0.0.1", 1, 1.0))
    assert rep["verdict"] == "FAIL" and rep["findings"][0]["code"] == "TALLY_UNREACHABLE"


def test_unknown_shape_reports_tags_seen():
    weird = "<ENVELOPE><OUTSTANDINGS><ENTRY><PARTYX>a</PARTYX><AMT>1</AMT></ENTRY></OUTSTANDINGS></ENVELOPE>"
    with FakeTally() as t:
        t.responder = responder(bills_body=weird)
        rep = check(t)
    f = next(f for f in rep["findings"] if f["code"] == "PARSER_SHAPE_UNKNOWN")
    assert rep["verdict"] == "FAIL" and "OUTSTANDINGS" in f["message"] and "ENTRY" in f["message"]


def test_valid_but_empty_is_partial():
    with FakeTally() as t:
        t.responder = responder(bills_body="<ENVELOPE></ENVELOPE>")
        rep = check(t)
    assert rep["verdict"] == "PARTIAL" and any(f["code"] == "ZERO_BILLS" for f in rep["findings"])


def test_warnings_for_duplicates_multiple_companies_and_failed_company_list():
    dup = flat_xml([bill(1), bill(1)])
    with FakeTally() as t:
        t.responder = responder(companies_ok=(), bills_body=dup)
        rep = check(t)
    codes = {f["code"] for f in rep["findings"]}
    assert {"DUPLICATE_BILLS", "COMPANY_LIST_FAILED"} <= codes and rep["verdict"] == "PARTIAL"


def test_custom_template_and_read_only_guard():
    good = "<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>Bills Receivable</ID></HEADER><BODY><DESC><STATICVARIABLES><SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY></STATICVARIABLES></DESC></BODY></ENVELOPE>"
    bad = "<ENVELOPE><HEADER><TALLYREQUEST>Import</TALLYREQUEST></HEADER><BODY><IMPORTDATA/></BODY></ENVELOPE>"
    with FakeTally() as t:
        t.responder = responder(bills_ok=())
        rep = check(t, company="A & B", bills_template=good)
        assert rep["bills"]["attempts"][-1]["variant"] == "custom_template" and rep["bills"]["attempts"][-1]["ok"] is False
        assert "A &amp; B" in t.requests[-1]
        rep2 = check(t, bills_template=bad)
    att = rep2["bills"]["attempts"][-1]
    assert att["variant"] == "custom_template" and att["ok"] is False and "ReadOnlyViolation" in att["error"]
    assert not any("IMPORTDATA" in r for r in t.requests)                              # refused before sending


def test_save_profile_keeps_other_fields(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"cloud_url": "https://x.example.com", "api_key_protected": "plain:AAAA", "tally_port": 9001, "tally_profile": {"companies": "data"}}))
    save_profile(str(cfg), {"bills": "data_plain"})
    d = json.loads(cfg.read_text())
    assert d["tally_profile"] == {"companies": "data", "bills": "data_plain"} and d["tally_port"] == 9001 and d["api_key_protected"] == "plain:AAAA"


def test_config_rejects_unknown_profile_variant():
    with pytest.raises(ConfigError, match="unknown request variant"):
        ConnectorConfig("https://x.example.com", KEY, tally_profile={"bills": "nope"}).validate()
    ConnectorConfig("https://x.example.com", KEY, tally_profile={"bills": "data_plain", "companies": "collection"}).validate()


def test_cli_tally_check_end_to_end_saves_profile(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    with FakeTally() as t:
        t.responder = responder(bills_ok=("data_plain",))
        cfg.write_text(json.dumps({"cloud_url": "https://x.example.com", "api_key": KEY, "tally_port": t.port}))
        code = cli.main(["tally-check", "--config", str(cfg), "--out-dir", str(tmp_path / "out"), "--save-profile"])
    out = capsys.readouterr().out
    assert code == 0 and "PASS" in out and "report folder" in out
    assert json.loads(cfg.read_text())["tally_profile"]["bills"] == "data_plain"
    assert KEY not in out and (tmp_path / "out" / "tally_check_report.json").exists()
    ConnectorConfig.load(str(cfg))                                                      # the saved config still loads


def test_cli_tally_check_exit_code_on_failure(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"cloud_url": "https://x.example.com", "api_key": KEY, "tally_port": 1}))
    assert cli.main(["tally-check", "--config", str(cfg), "--out-dir", str(tmp_path / "o")]) == 1
    assert "TALLY_UNREACHABLE" in capsys.readouterr().out


FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "tally")
REAL = sorted(f for f in (os.listdir(FIXTURES) if os.path.isdir(FIXTURES) else []) if f.startswith("real_") and f.endswith(".xml"))


@pytest.mark.skipif(not REAL, reason="no real Tally captures yet (tests/fixtures/tally/real_*.xml)")
@pytest.mark.parametrize("name", REAL)
def test_real_capture_parses(name):
    """A redacted capture from a real TallyPrime must parse into usable bills with no unreadable rows."""
    from dueclock.core.tally_parse import parse_tally_response
    res = parse_tally_response(open(os.path.join(FIXTURES, name), encoding="utf-8").read())
    assert res.records and not res.errors, res.errors[:3]
    assert all(re.match(r"\d{4}-\d{2}-\d{2}$", r["invoice_date"]) and r["pending_amount"] >= 0 for r in res.records)


# ---- demo Tally + fixture import
def test_demo_tally_passes_the_whole_check_and_the_connector_syncs_from_it(tmp_path):
    import threading
    from datetime import date
    from dueclock.connector import demo_tally
    srv = demo_tally.make_server(0, today=date(2026, 11, 10))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        rep = run_check(TallyClient("127.0.0.1", srv.server_address[1], 5.0), out_dir=str(tmp_path))
        assert rep["verdict"] == "PASS" and rep["bills"]["chosen"] == "data_explode" and rep["companies"]["chosen"] == "collection"
        st = next(a for a in rep["bills"]["attempts"] if a["variant"] == "data_explode")["stats"]
        assert st["records"] == 15 and st["buyers"] == 5 and st["duplicate_keys"] == 0
        blob = "".join(open(c, encoding="utf-8").read() for c in rep["captures"])
        assert "Zenith" not in blob and "DEMO/001" not in blob                         # captures stay redacted
    finally:
        srv.shutdown(); srv.server_close()


@pytest.mark.parametrize("kw", [dict(utf16=True), dict(amount_style="dr"), dict(amount_style="negative")])
def test_demo_tally_variants_are_all_understood(kw):
    import threading
    from datetime import date
    from dueclock.connector import demo_tally
    srv = demo_tally.make_server(0, today=date(2026, 11, 10), **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        res = TallyClient("127.0.0.1", srv.server_address[1], 5.0).fetch_bills()
        assert len(res.records) == 15 and not res.errors and all(r["pending_amount"] > 0 for r in res.records)
    finally:
        srv.shutdown(); srv.server_close()


def test_demo_tally_plain_only_rehearses_the_fallback_variant():
    import threading
    from datetime import date
    from dueclock.connector import demo_tally
    srv = demo_tally.make_server(0, today=date(2026, 11, 10), plain_only=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        rep = run_check(TallyClient("127.0.0.1", srv.server_address[1], 5.0))
        assert rep["bills"]["chosen"] == "data_plain" and rep["profile"]["bills"] == "data_plain"
        assert next(a for a in rep["bills"]["attempts"] if a["variant"] == "data_explode")["ok"] is False
    finally:
        srv.shutdown(); srv.server_close()


def test_demo_tally_is_read_only():
    import threading
    import httpx
    from dueclock.connector import demo_tally
    srv = demo_tally.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = httpx.post(f"http://127.0.0.1:{srv.server_address[1]}", content="<ENVELOPE><IMPORTDATA/></ENVELOPE>")
        assert "read-only" in r.text
    finally:
        srv.shutdown(); srv.server_close()


def test_import_fixture_redacts_validates_and_replays(tmp_path):
    from dueclock.connector.tally_check import import_fixture
    from dueclock.core.tally_parse import parse_tally_response
    src = tmp_path / "capture.xml"
    src.write_text(flat_xml(BILLS), encoding="utf-8")                                   # NOT redacted: the import must make it safe anyway
    res = import_fixture(str(src), str(tmp_path / "fx"), "prime-test")
    assert res["ok"] and res["records"] == 3 and res["path"].endswith("real_prime-test.xml")
    saved = open(res["path"], encoding="utf-8").read()
    assert "Zenith" not in saved and "Acme" not in saved and "INV/1" not in saved
    again = parse_tally_response(saved)
    assert len(again.records) == 3 and not again.errors and sum(r["pending_amount"] for r in again.records) == 175000.0


def test_import_fixture_refuses_unreadable_captures_and_bad_names(tmp_path):
    from dueclock.connector.tally_check import import_fixture
    junk = tmp_path / "junk.xml"
    junk.write_text("<ENVELOPE><OUTSTANDINGS/></ENVELOPE>")
    res = import_fixture(str(junk), str(tmp_path / "fx"), "x")
    assert not res["ok"] and "no bills" in res["problems"][0] and not (tmp_path / "fx").exists()
    with pytest.raises(ValueError):
        import_fixture(str(junk), str(tmp_path / "fx"), "../../etc")


def test_cli_tally_fixture(tmp_path, capsys):
    src = tmp_path / "c.xml"
    src.write_text(flat_xml(BILLS), encoding="utf-8")
    assert cli.main(["tally-fixture", str(src), "--name", "v1", "--dest", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "real_v1.xml").exists() and '"ok": true' in capsys.readouterr().out
    assert cli.main(["tally-fixture", str(src)]) == 2                                   # name is required
    assert cli.main(["tally-fixture", str(tmp_path / "missing.xml"), "--name", "v"]) == 2
