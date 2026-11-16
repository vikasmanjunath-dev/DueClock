"""Tally XML and export parsing.

Fixes a defect in the prototype parser: ``elem.find("A") or elem.find("B")`` treats a leaf element as
falsy (ElementTree elements are falsy when they have no children), so the first tag was skipped.
Here every lookup uses explicit ``is not None`` checks.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

BILL_TAGS = ("BILLOUTSTANDING", "BILL", "VOUCHER")
DATE_TAGS = ("BILLDATE", "DATE")
REF_TAGS = ("BILLREF", "BILLNAME", "VOUCHERNUMBER", "REFERENCE")
PARTY_TAGS = ("BILLPARTY", "PARTYLEDGERNAME", "PARTYNAME")
OPEN_TAGS = ("BILLOPENING", "BILLOPENGING", "OPENINGBALANCE", "AMOUNT")
CLOSE_TAGS = ("BILLCL", "CLOSINGBALANCE", "PENDINGAMOUNT")
CREDIT_TAGS = ("BILLCREDITPERIOD",)
GUID_TAGS = ("GUID",)
DATE_FORMATS = ("%Y%m%d", "%d-%b-%y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")


@dataclass
class ParseResult:
    records: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def parse_tally_date(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    s = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(text: Optional[str]) -> Optional[float]:
    """Tally shows debit/credit signs, commas and currency marks. Returns the absolute rupee amount."""
    if text is None:
        return None
    s = re.sub(r"(?i)(rs\.?|inr|₹|dr|cr|,|\s)", "", text.strip())
    s = s.strip("()")
    if not s:
        return None
    try:
        return abs(float(s))
    except ValueError:
        return None


def _first(node: ET.Element, tags) -> Optional[str]:
    for t in tags:
        el = node.find(t)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
    return None


def _has(node: ET.Element, tags) -> bool:
    return any(node.find(t) is not None for t in tags)


def _parse_flat(root: ET.Element) -> ParseResult:
    """'Bills Receivable' report shape: BILLFIXED (date, ref, party) followed by sibling BILLCL / BILLDUE elements.
    The original bill amount is not in this report, so records carry amount_known=False. [A: confirm on TallyPrime]"""
    out = ParseResult()
    cur: Optional[Dict[str, Any]] = None

    def close():
        nonlocal cur
        if cur is None:
            return
        if cur.get("pending") is None:
            out.errors.append(f"bill {cur.get('invoice_number')}: no closing balance")
        else:
            due, d = cur.get("due"), cur["invoice_date"]
            out.records.append({
                "guid": None, "invoice_number": cur["invoice_number"], "invoice_date": d.isoformat(), "buyer_name": cur["buyer_name"],
                "amount": cur["pending"], "pending_amount": cur["pending"], "amount_known": False,
                "agreed_credit_days": max(0, (due - d).days) if due else 0,
            })
        cur = None

    for el in root.iter():
        if el.tag == "BILLFIXED":
            close()
            d, ref, party = parse_tally_date(_first(el, DATE_TAGS)), _first(el, REF_TAGS), _first(el, PARTY_TAGS)
            if d is None or not ref or not party:
                out.errors.append("bill with missing date, reference or party")
                cur = None
                continue
            cur = {"invoice_date": d, "invoice_number": ref, "buyer_name": party, "pending": None, "due": None}
        elif cur is not None and el.tag == "BILLCL":
            cur["pending"] = parse_amount(el.text)
        elif cur is not None and el.tag == "BILLDUE":
            cur["due"] = parse_tally_date(el.text)
    close()
    return out


def parse_tally_response(xml_content: str) -> ParseResult:
    """Dispatch on the response shape: flat report (BILLFIXED) or nested bill nodes."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        r = ParseResult()
        r.errors.append(f"invalid XML: {e}")
        return r
    if root.find(".//BILLFIXED") is not None:
        return _parse_flat(root)
    return parse_bills_xml(xml_content)


def parse_bills_xml(xml_content: str) -> ParseResult:
    out = ParseResult()
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        out.errors.append(f"invalid XML: {e}")
        return out
    nodes: List[ET.Element] = []
    for tag in BILL_TAGS:
        nodes = root.findall(f".//{tag}")
        if nodes:
            break
    for i, node in enumerate(nodes):
        ref = _first(node, REF_TAGS)
        party = _first(node, PARTY_TAGS)
        d = parse_tally_date(_first(node, DATE_TAGS))
        opening = parse_amount(_first(node, OPEN_TAGS))
        closing = parse_amount(_first(node, CLOSE_TAGS)) if _has(node, CLOSE_TAGS) else None
        if not ref or not party or d is None or not opening:
            out.errors.append(f"bill {i + 1}: missing reference, party, date or amount")
            continue
        pending = closing if closing is not None else opening
        credit = 0
        raw_credit = _first(node, CREDIT_TAGS)
        if raw_credit:
            m = re.search(r"\d+", raw_credit)
            credit = int(m.group()) if m else 0
        out.records.append({
            "guid": _first(node, GUID_TAGS), "invoice_number": ref, "invoice_date": d.isoformat(), "buyer_name": party,
            "amount": opening, "pending_amount": min(pending, opening), "agreed_credit_days": credit,
        })
    if not nodes:
        out.errors.append("no bill nodes found")
    return out


ALIASES = {
    "invoice_number": ("invoice_number", "invoice no", "bill ref", "reference", "voucher no"),
    "invoice_date": ("invoice_date", "date", "bill date"),
    "buyer_name": ("buyer_name", "party", "party name", "customer"),
    "amount": ("amount", "bill amount", "opening"),
    "pending_amount": ("pending_amount", "pending", "outstanding", "closing"),
    "agreed_credit_days": ("agreed_credit_days", "credit days", "credit period"),
    "buyer_gstin": ("buyer_gstin", "gstin"),
    "buyer_mobile": ("buyer_mobile", "mobile", "phone"),
}


def parse_export_rows(rows: List[Dict[str, Any]]) -> ParseResult:
    """Fallback import from a Tally export converted to rows (CSV/Excel/JSON)."""
    out = ParseResult()
    for i, row in enumerate(rows):
        lower = {str(k).strip().lower(): v for k, v in row.items()}

        def pick(name: str):
            for a in ALIASES[name]:
                if a in lower and str(lower[a]).strip() != "":
                    return lower[a]
            return None
        d = parse_tally_date(str(pick("invoice_date"))) if pick("invoice_date") is not None else None
        amount = parse_amount(str(pick("amount"))) if pick("amount") is not None else None
        pending = parse_amount(str(pick("pending_amount"))) if pick("pending_amount") is not None else amount
        ref, party = pick("invoice_number"), pick("buyer_name")
        if not ref or not party or d is None or not amount:
            out.errors.append(f"row {i + 1}: missing invoice number, party, date or amount")
            continue
        credit = pick("agreed_credit_days")
        m = re.search(r"\d+", str(credit)) if credit is not None else None
        out.records.append({
            "invoice_number": str(ref).strip(), "invoice_date": d.isoformat(), "buyer_name": str(party).strip(), "amount": amount,
            "pending_amount": min(pending if pending is not None else amount, amount), "agreed_credit_days": int(m.group()) if m else 0,
            "buyer_gstin": pick("buyer_gstin"), "buyer_mobile": pick("buyer_mobile"),
        })
    return out
