from datetime import date

from dueclock.core.tally_parse import parse_amount, parse_bills_xml, parse_export_rows, parse_tally_date

XML = """<ENVELOPE><BODY><DATA><COLLECTION>
<BILLOUTSTANDING><GUID>g-1</GUID><BILLDATE>20261105</BILLDATE><BILLREF>INV/041</BILLREF><BILLPARTY>Zenith Tooling</BILLPARTY>
 <BILLOPENING>1,45,000.00 Dr</BILLOPENING><BILLCL>45,000.00 Dr</BILLCL><BILLCREDITPERIOD>45 Days</BILLCREDITPERIOD></BILLOUTSTANDING>
<BILLOUTSTANDING><BILLDATE>5-Dec-26</BILLDATE><BILLREF>INV/042</BILLREF><BILLPARTY>Kaveri Hydraulics</BILLPARTY>
 <BILLOPENING>85,000.00 Dr</BILLOPENING><BILLCL>0.00 Dr</BILLCL></BILLOUTSTANDING>
<BILLOUTSTANDING><BILLDATE>20261106</BILLDATE><BILLREF>INV/043</BILLREF><BILLOPENING>10,000 Dr</BILLOPENING></BILLOUTSTANDING>
</COLLECTION></DATA></BODY></ENVELOPE>"""


def test_parses_bills_amounts_and_credit_period():
    r = parse_bills_xml(XML)
    assert len(r.records) == 2 and len(r.errors) == 1
    a, b = r.records
    assert a["invoice_number"] == "INV/041" and a["invoice_date"] == "2026-11-05" and a["guid"] == "g-1"
    assert a["amount"] == 145000.0 and a["pending_amount"] == 45000.0 and a["agreed_credit_days"] == 45
    assert b["invoice_date"] == "2026-12-05" and b["pending_amount"] == 0.0   # explicit zero closing = paid


def test_regression_leaf_element_is_not_skipped():
    """The prototype's `find(A) or find(B)` skipped leaf elements; a bill with only BILLDATE must parse."""
    r = parse_bills_xml("<R><BILLOUTSTANDING><BILLDATE>20270101</BILLDATE><BILLREF>X1</BILLREF><BILLPARTY>P</BILLPARTY><BILLOPENING>100</BILLOPENING></BILLOUTSTANDING></R>")
    assert r.records and r.records[0]["invoice_date"] == "2027-01-01"


def test_invalid_and_empty_xml_report_errors():
    assert parse_bills_xml("<broken").errors
    assert parse_bills_xml("<R></R>").errors == ["no bill nodes found"]


def test_date_and_amount_formats():
    for s in ("20270131", "31-Jan-27", "31-Jan-2027", "31/01/2027", "2027-01-31", "31-01-2027"):
        assert parse_tally_date(s) == date(2027, 1, 31)
    assert parse_tally_date("garbage") is None and parse_tally_date(None) is None
    assert parse_amount("1,45,000.50 Dr") == 145000.5 and parse_amount("Rs. 2,000 Cr") == 2000.0 and parse_amount("(500.00)") == 500.0
    assert parse_amount("") is None and parse_amount("abc") is None


def test_export_rows_fallback_with_aliases():
    r = parse_export_rows([
        {"Bill Ref": "A1", "Date": "01/01/2027", "Party Name": "Acme", "Amount": "10,000", "Pending": "4,000", "Credit Days": "45 days", "GSTIN": "29AAAAA0000A1Z5"},
        {"Bill Ref": "A2", "Date": "bad", "Party Name": "Acme", "Amount": "1"},
    ])
    assert len(r.records) == 1 and len(r.errors) == 1
    rec = r.records[0]
    assert rec["pending_amount"] == 4000.0 and rec["agreed_credit_days"] == 45 and rec["buyer_gstin"] == "29AAAAA0000A1Z5"
