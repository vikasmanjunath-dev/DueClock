from datetime import date

import pytest

from dueclock.core.bank_import import parse_csv, parse_rows
from dueclock.core.matching import alias_key, name_similarity, parse_narration, reference_hit, suggest


def inv(i, number, d, buyer, name, rupees):
    return {"id": i, "invoice_number": number, "invoice_date": d, "buyer_id": buyer, "buyer_name": name, "effective_paise": int(rupees * 100)}


def cr(amount, d="2027-01-18", narration="", payer=None):
    return {"txn_date": d, "amount_paise": int(amount * 100), "narration": narration, "payer_name": payer}


ZEN = "Zenith Tooling Systems Pvt Ltd"
INVS = [inv("a", "INV/1", "2027-01-01", "zen", ZEN, 100000), inv("b", "INV/2", "2027-01-05", "zen", ZEN, 50000),
        inv("c", "INV/3", "2027-01-02", "kav", "Kaveri Hydraulics LLP", 75000)]


# ---------------- narration parsing
def test_parse_upi_neft_imps():
    u = parse_narration("UPI/412345678901/ZENITH TOOLING SYSTEMS/zenith@icici/INV 1")
    assert (u.channel, u.utr, u.vpa) == ("UPI", "412345678901", "zenith@icici") and u.name == "ZENITH TOOLING SYSTEMS"
    n = parse_narration("NEFT-HDFCN52027011112345-KAVERI HYDRAULICS LLP-INV/3")
    assert n.channel == "NEFT" and n.utr == "HDFCN52027011112345" and n.name and "KAVERI" in n.name
    i = parse_narration("IMPS/P2A/401234567890/PRECISION METAL")
    assert i.channel == "IMPS" and i.utr == "401234567890" and "PRECISION" in (i.name or "")
    e = parse_narration(None)
    assert (e.channel, e.utr, e.vpa, e.name) == ("OTHER", None, None, None)


def test_name_similarity():
    assert name_similarity("ZENITH TOOLING SYSTEMS", ZEN) >= 0.9
    assert name_similarity("Zenith Tooling", ZEN) >= 0.9                      # all tokens of the shorter name are in the longer
    assert name_similarity("Zenith Tooling Systems", "Zenith Tooling Systems Pvt. Ltd.") >= 0.9
    assert name_similarity("Random Person", ZEN) < 0.3 and name_similarity(None, ZEN) == 0 and name_similarity("Pvt Ltd", "Pvt Ltd") == 0
    assert alias_key("Acme Traders Pvt Ltd") == alias_key("ACME  TRADERS")


def test_reference_hit_is_exact_not_prefix():
    assert reference_hit("UPI paid INV/1 thanks", "INV/1")
    assert reference_hit("payment for inv 2026 028", "INV/2026/028") and reference_hit("INV2026028 paid", "INV/2026/028")
    assert not reference_hit("payment inv 12", "INV/1") and reference_hit("payment inv 1 done", "INV/1")
    assert not reference_hit("anything", "A1") and not reference_hit(None, "INV/1")


# ---------------- suggestions
def test_exact_amount_reference_and_name_scores_highest():
    s = suggest(cr(100000, narration="UPI/412345678901/ZENITH TOOLING SYSTEMS/zenith@icici/INV/1"), INVS)
    top = s[0]
    assert [a["invoice_id"] for a in top.allocations] == ["a"] and top.allocations[0]["paise"] == 10000000
    assert top.score >= 0.95 and {"INVOICE_NUMBER_IN_NARRATION", "AMOUNT_EXACT", "PAYER_NAME_MATCH"} <= set(top.reasons)


def test_lump_sum_over_one_buyers_invoices():
    s = suggest(cr(150000, narration="NEFT-HDFCN52027011812345-ZENITH TOOLING SYSTEMS"), INVS)
    assert {a["invoice_id"] for a in s[0].allocations} == {"a", "b"} and sum(a["paise"] for a in s[0].allocations) == 15000000
    assert "AMOUNT_EXACT" in s[0].reasons and s[0].score >= 0.6


def test_tds_short_payment_is_accepted_within_tolerance():
    s = suggest(cr(73500, narration="NEFT-HDFCN52027011912345-KAVERI HYDRAULICS"), INVS)      # 2% short of 75,000
    assert s and s[0].allocations == [{"invoice_id": "c", "invoice_number": "INV/3", "paise": 7350000}] and "AMOUNT_WITHIN_TDS_TOLERANCE" in s[0].reasons
    far = suggest(cr(60000, narration="NEFT-X-KAVERI HYDRAULICS"), INVS)
    assert far and "PARTIAL_OR_ON_ACCOUNT" in far[0].reasons and far[0].allocations[0]["paise"] == 6000000


def test_ineligible_invoices_are_ignored():
    assert suggest(cr(100000, d="2026-12-31", narration="ZENITH TOOLING SYSTEMS INV/1"), INVS) == []          # credit before the invoice date
    old = [inv("z", "INV/9", "2025-01-01", "zen", ZEN, 1000)]
    assert suggest(cr(1000, narration="ZENITH TOOLING SYSTEMS INV/9"), old) == []                         # older than a year


def test_unique_amount_only_when_unique():
    one = [inv("a", "X-1", "2027-01-01", "b1", "Alpha", 777), inv("b", "X-2", "2027-01-01", "b2", "Beta", 888)]
    s = suggest(cr(777, narration="UPI/1/UNKNOWN PAYER"), one)
    assert s and s[0].reasons[0] == "UNIQUE_AMOUNT"
    twin = one + [inv("c", "X-3", "2027-01-01", "b3", "Gamma", 777)]
    assert suggest(cr(777, narration="UPI/1/UNKNOWN PAYER"), twin) == []


def test_learned_alias_identifies_the_buyer():
    s = suggest(cr(50000, narration="UPI/1/ZTS ACCOUNTS", payer="ACME HOLDINGS"), INVS, {alias_key("ACME HOLDINGS"): "zen"})
    assert s and s[0].allocations[0]["invoice_id"] == "b" and "LEARNED_PAYER" in s[0].reasons


def test_invoice_one_is_not_confused_with_invoice_twelve():
    invs = [inv("a", "INV/1", "2027-01-01", "x", "Acme", 100), inv("b", "INV/12", "2027-01-01", "y", "Beta", 100)]
    s = suggest(cr(100, narration="paid inv/12 acme"), invs)
    assert s[0].allocations[0]["invoice_id"] == "b"


def test_many_invoices_do_not_explode():
    many = [inv(f"i{n}", f"I-{n}", "2027-01-01", "zen", ZEN, 1000 + n) for n in range(20)]
    s = suggest(cr(1003 + 1004, narration="ZENITH TOOLING SYSTEMS"), many)
    assert s and len(s[0].allocations) == 2


def test_results_are_deterministic_and_limited():
    a = suggest(cr(150000, narration="ZENITH TOOLING SYSTEMS"), INVS, max_suggestions=2)
    b = suggest(cr(150000, narration="ZENITH TOOLING SYSTEMS"), INVS, max_suggestions=2)
    assert [x.allocations for x in a] == [x.allocations for x in b] and len(a) <= 2


# ---------------- bank statement import
CSV = """Account Statement for 123456789
Period: 01-Jan-2027 to 31-Jan-2027

Date,Narration,Chq/Ref No,Withdrawal Amt,Deposit Amt,Balance
18/01/27,UPI/412345678901/ZENITH TOOLING SYSTEMS/zenith@icici/INV/1,412345678901,,"1,00,000.00","5,00,000.00"
19/01/27,NEFT-HDFCN52027011912345-KAVERI HYDRAULICS,HDFCN52027011912345,,73500.00,"5,73,500.00"
19/01/27,ATM WITHDRAWAL,,5000.00,,"5,68,500.00"
20/01/27,UPI/555555555555/SAME PAYER,,,1000.00,x
20/01/27,UPI/555555555555/SAME PAYER,,,1000.00,x
not a date,GARBAGE,,,10,x
"""


def test_csv_import_with_preamble_debits_and_duplicates():
    r = parse_csv(CSV)
    assert [c["amount_paise"] for c in r.credits] == [10000000, 7350000, 100000, 100000] and r.ignored_debits == 1
    assert r.credits[0]["txn_date"] == "2027-01-18" and r.credits[0]["utr"] == "412345678901" and r.credits[0]["payer_vpa"] == "zenith@icici"
    keys = [c["dedupe_key"] for c in r.credits]
    assert len(set(keys)) == 4 and keys[2].split("#")[0] == keys[3].split("#")[0]        # two identical rows stay two credits
    assert any("date not understood" in e for e in r.errors)
    assert [c["dedupe_key"] for c in parse_csv(CSV).credits] == keys                      # stable across imports


def test_csv_without_header_and_json_rows():
    assert parse_csv("hello\nworld").errors == ["no header row with a date and a narration column was found"]
    r = parse_rows([{"date": "2027-01-18", "amount": "1,000", "narration": "UPI/123456789012/A"}, {"date": "x", "amount": 1, "narration": "n"}, {"date": "2027-01-18", "narration": "no amount"}])
    assert len(r.credits) == 1 and r.credits[0]["utr"] == "123456789012" and len(r.errors) == 2
