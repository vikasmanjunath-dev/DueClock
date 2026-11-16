"""Evaluate one stored invoice row with the clock engine (shared by the API and the reminder engine)."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from .clock import ClockResult, InvoiceFacts, SupplierFacts, evaluate
from .rates import RateTable
from .store import to_rupees


def _d(text: Optional[str]) -> Optional[date]:
    return date.fromisoformat(text) if text else None


def evaluate_row(row: Dict[str, Any], supplier: Optional[Dict[str, Any]], as_of: date, rates: RateTable) -> ClockResult:
    sup = SupplierFacts(
        category=(supplier or {}).get("category") or "UNKNOWN", activity=(supplier or {}).get("activity") or "UNKNOWN",
        udyam_registration_date=_d((supplier or {}).get("udyam_registration_date")),
    )
    facts = InvoiceFacts(
        invoice_date=date.fromisoformat(row["invoice_date"]), delivery_date=_d(row["delivery_date"]), acceptance_date=_d(row.get("acceptance_date")),
        outstanding=to_rupees(max(0, row["outstanding_paise"] - int(row.get("unposted_paise") or 0))), has_written_agreement=bool(row["buyer_agreement"]), agreed_credit_days=int(row["agreed_credit_days"] or row.get("buyer_agreed_days") or 0),
    )
    return evaluate(facts, sup, as_of, rates)
