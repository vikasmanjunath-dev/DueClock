"""Year-end exposure scan (buyer side): which vendor bills could lose the deduction under Section 43B(h)?

Input: a payables list (CSV or Tally XML). Output: for each bill, its statutory due date and where it stands against the
financial-year end, plus honest totals:
- confirmed: vendors known to be Micro/Small manufacturers or service providers
- possible : vendors whose status is unknown, assuming the 15-day rule (an upper bound, shown separately)
Uses the same rules engine as the seller side (clock.py). The reviewer-pending 43B(h) payment-basis rule applies (see README).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from .bank_import import _date
from .clock import InvoiceFacts, SupplierFacts, assess_coverage, Coverage, evaluate, financial_year, financial_year_end
from .rates import RateTable
from .tally_parse import parse_amount, parse_tally_response

MAX_ROWS = 5000
VENDOR = ("vendor", "vendor name", "supplier", "supplier name", "party", "party name", "name", "creditor")
BILLNO = ("bill no", "bill number", "invoice no", "invoice number", "reference", "ref", "bill ref", "voucher no", "bill")
BILLDATE = ("bill date", "invoice date", "date", "voucher date", "purchase date")
AMOUNT = ("amount", "bill amount", "invoice amount", "gross amount", "total")
PENDING = ("pending", "outstanding", "balance", "pending amount", "amount pending", "closing")
PAID = ("paid", "amount paid", "paid amount", "payment")
UDYAM = ("udyam", "udyam no", "udyam number", "udyam registration", "udyam reg no")
CATEGORY = ("category", "enterprise type", "enterprise category", "size", "msme category", "msme type")
ACTIVITY = ("activity", "nature", "nature of business", "enterprise activity", "business type", "type of activity")
CREDIT = ("credit days", "agreed days", "agreed credit days", "terms", "credit period", "payment terms", "agreed terms (days)")
GSTIN = ("gstin", "gst no", "vendor gstin")

PAST_LIMIT, CROSSES, WITHIN, NOT_COVERED, UNVERIFIED, SETTLED = "PAST_LIMIT", "CROSSES_BY_YEAR_END", "WITHIN_LIMIT", "NOT_COVERED", "UNVERIFIED", "SETTLED"
LABEL = {PAST_LIMIT: "Past the limit now", CROSSES: "Will pass the limit before year end", WITHIN: "Within the limit at year end", NOT_COVERED: "Not covered",
         UNVERIFIED: "Vendor status unknown", SETTLED: "Settled"}


@dataclass(frozen=True)
class VendorStatus:
    category: str = "UNKNOWN"
    activity: str = "UNKNOWN"


def norm_category(text: Optional[str]) -> str:
    t = (text or "").lower()
    for key, val in (("micro", "MICRO"), ("small", "SMALL"), ("medium", "MEDIUM"), ("large", "LARGE")):
        if key in t:
            return val
    return "UNKNOWN"


def norm_activity(text: Optional[str]) -> str:
    t = (text or "").lower()
    if any(k in t for k in ("trad", "wholesale", "retail", "dealer", "distribut")):
        return "TRADING"
    if "manufact" in t:
        return "MANUFACTURING"
    if "servic" in t:
        return "SERVICES"
    return "UNKNOWN"


def vendor_key(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split())


def _pick(row: Dict[str, Any], names) -> Optional[str]:
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def parse_payables_csv(text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if any(k in ln.lower() for k in ("vendor", "supplier", "party", "creditor")) and "date" in ln.lower()), None)
    if start is None:
        return [], ["No header row found. The first row needs columns such as Vendor, Bill Date and Amount."]
    rows = [{re.sub(r"\s+", " ", (k or "").strip().lower()): v for k, v in r.items()} for r in csv.DictReader(io.StringIO("\n".join(lines[start:])))]
    return _records(rows)


def _records(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    out, errors = [], []
    for i, r in enumerate(rows[:MAX_ROWS], start=1):
        if not any(str(v or "").strip() for v in r.values()):
            continue
        vendor, d = _pick(r, VENDOR), _date(_pick(r, BILLDATE))
        amount = parse_amount(_pick(r, AMOUNT)) if _pick(r, AMOUNT) else None
        pending = parse_amount(_pick(r, PENDING)) if _pick(r, PENDING) else None
        paid = parse_amount(_pick(r, PAID)) if _pick(r, PAID) else None
        if pending is None:
            pending = max(0.0, (amount or 0) - (paid or 0)) if amount else None
        if amount is None:
            amount = pending
        if not vendor or d is None or amount is None or pending is None:
            errors.append(f"Row {i}: needs a vendor, a bill date and an amount.")
            continue
        credit = _pick(r, CREDIT)
        m = re.search(r"\d+", credit) if credit else None
        out.append({"vendor": vendor[:120], "bill_no": (_pick(r, BILLNO) or "")[:60], "bill_date": d, "amount": float(amount), "pending": float(min(pending, amount)),
                    "udyam": _pick(r, UDYAM), "category": norm_category(_pick(r, CATEGORY)), "activity": norm_activity(_pick(r, ACTIVITY)),
                    "agreed_days": int(m.group()) if m else 0, "gstin": _pick(r, GSTIN)})
    if len(rows) > MAX_ROWS:
        errors.append(f"Only the first {MAX_ROWS} rows were read.")
    return out, errors


def parse_payables_xml(xml: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    parsed = parse_tally_response(xml)
    rows = [{"vendor": r["buyer_name"], "bill no": r["invoice_number"], "bill date": r["invoice_date"], "amount": r["amount"], "pending": r["pending_amount"],
             "credit days": str(r.get("agreed_credit_days") or "")} for r in parsed.records]
    recs, errs = _records(rows)
    return recs, parsed.errors + errs


@dataclass
class ScanResult:
    as_of: date
    fy_end: date
    fy_label: str
    summary: Dict[str, Any]
    vendors: List[Dict[str, Any]]
    bills: List[Dict[str, Any]]
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"as_of": self.as_of.isoformat(), "fy_end": self.fy_end.isoformat(), "fy_label": self.fy_label, "summary": self.summary,
                "vendors": self.vendors, "bills": self.bills, "errors": self.errors}


def run_scan(records: List[Dict[str, Any]], as_of: date, rates: Optional[RateTable] = None, *, errors: Optional[List[str]] = None,
             default_agreed_days: int = 0, status_lookup: Optional[Callable[[Dict[str, Any]], Optional[VendorStatus]]] = None) -> ScanResult:
    """``status_lookup`` is the hook for a verification API (D-22: manual for pilots). It returns a VendorStatus or None."""
    rates = rates or RateTable.default()
    fy_end = financial_year_end(as_of)
    bills: List[Dict[str, Any]] = []
    for r in records:
        status_src = VendorStatus(r["category"], r["activity"])
        if status_lookup and (status_src.category == "UNKNOWN" or status_src.activity == "UNKNOWN"):
            looked = status_lookup(r)
            if looked:
                status_src = looked
        cov = assess_coverage(SupplierFacts(status_src.category, status_src.activity))
        agreed = r["agreed_days"] if 0 < r["agreed_days"] <= 45 else default_agreed_days
        facts = InvoiceFacts(invoice_date=r["bill_date"], delivery_date=r["bill_date"], outstanding=r["pending"], has_written_agreement=agreed > 0, agreed_credit_days=agreed)
        base = {"vendor": r["vendor"], "bill_no": r["bill_no"], "bill_date": r["bill_date"].isoformat(), "amount": r["amount"], "outstanding": r["pending"]}
        if r["pending"] <= 0:
            bills.append({**base, "status": SETTLED, "due_date": None, "days": None, "at_risk": 0.0, "interest": 0.0})
            continue
        if cov in (Coverage.NOT_COVERED_TRADER, Coverage.NOT_COVERED_SIZE):
            bills.append({**base, "status": NOT_COVERED, "due_date": None, "days": None, "at_risk": 0.0, "interest": 0.0})
            continue
        sup = SupplierFacts("MICRO", "MANUFACTURING") if cov == Coverage.UNKNOWN else SupplierFacts(status_src.category, status_src.activity)
        today = evaluate(facts, sup, as_of, rates)
        if today.overdue_days > 0:
            st, days = PAST_LIMIT, -today.overdue_days
        elif today.due_date <= fy_end:
            st, days = CROSSES, today.days_remaining
        else:
            st, days = WITHIN, today.days_remaining
        known = cov == Coverage.COVERED
        bills.append({**base, "status": st if known else UNVERIFIED, "would_be": st, "due_date": today.due_date.isoformat(), "days": days,
                      "at_risk": r["pending"] if st in (PAST_LIMIT, CROSSES) else 0.0, "interest": today.interest if known and st == PAST_LIMIT else 0.0})

    def total(pred) -> Tuple[float, int]:
        sel = [b for b in bills if pred(b)]
        return round(sum(b["outstanding"] for b in sel), 2), len(sel)

    past_amt, past_n = total(lambda b: b["status"] == PAST_LIMIT)
    cross_amt, cross_n = total(lambda b: b["status"] == CROSSES)
    poss_amt, poss_n = total(lambda b: b["status"] == UNVERIFIED and b.get("would_be") in (PAST_LIMIT, CROSSES))
    vendors: Dict[str, Dict[str, Any]] = {}
    for b in bills:
        v = vendors.setdefault(vendor_key(b["vendor"]), {"vendor": b["vendor"], "bills": 0, "outstanding": 0.0, "confirmed_at_risk": 0.0, "possible_at_risk": 0.0, "status": b["status"]})
        v["bills"] += 1
        v["outstanding"] = round(v["outstanding"] + b["outstanding"], 2)
        if b["status"] in (PAST_LIMIT, CROSSES):
            v["confirmed_at_risk"] = round(v["confirmed_at_risk"] + b["outstanding"], 2)
        if b["status"] == UNVERIFIED and b.get("would_be") in (PAST_LIMIT, CROSSES):
            v["possible_at_risk"] = round(v["possible_at_risk"] + b["outstanding"], 2)
        if b["status"] in (PAST_LIMIT, CROSSES, UNVERIFIED, NOT_COVERED):
            v["status"] = b["status"] if v["status"] in (SETTLED, WITHIN) else v["status"]
    vlist = sorted(vendors.values(), key=lambda v: (-v["confirmed_at_risk"], -v["possible_at_risk"], v["vendor"]))
    pay_by = sorted((b for b in bills if b["status"] in (PAST_LIMIT, CROSSES)), key=lambda b: (b["due_date"], -b["outstanding"]))[:10]
    summary = {
        "bills": len(bills), "vendors": len(vlist), "total_outstanding": round(sum(b["outstanding"] for b in bills), 2),
        "past_limit": {"amount": past_amt, "bills": past_n}, "crosses_by_year_end": {"amount": cross_amt, "bills": cross_n},
        "confirmed_at_risk": round(past_amt + cross_amt, 2), "possible_at_risk": {"amount": poss_amt, "bills": poss_n},
        "vendors_unverified": sum(1 for v in vlist if v["status"] == UNVERIFIED), "vendors_not_covered": sum(1 for v in vlist if v["status"] == NOT_COVERED),
        "pay_first": [{"vendor": b["vendor"], "bill_no": b["bill_no"], "due_date": b["due_date"], "outstanding": b["outstanding"], "status": b["status"]} for b in pay_by],
    }
    return ScanResult(as_of, fy_end, financial_year(as_of), summary, vlist, bills, errors or [])


_FORMULA = re.compile(r"^[=+\-@\t\r]")


def csv_safe(value: Any) -> Any:
    """Stop spreadsheet formula injection in exported cells."""
    return "'" + value if isinstance(value, str) and _FORMULA.match(value) else value


def to_csv(result: ScanResult) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Vendor", "Bill no", "Bill date", "Outstanding", "Status", "Statutory due date", "Days (negative = overdue)", "Amount at risk", "Interest (MSMED s.16)"])
    for b in result.bills:
        w.writerow([csv_safe(b["vendor"]), csv_safe(b["bill_no"]), b["bill_date"], b["outstanding"], LABEL.get(b["status"], b["status"]), b["due_date"] or "", b["days"] if b["days"] is not None else "",
                    b["at_risk"], b["interest"]])
    w.writerow([])
    w.writerow([f"As of {result.as_of.isoformat()}; financial year end {result.fy_end.isoformat()}. Computed for your CA to review. This is not legal advice."])
    return out.getvalue()
