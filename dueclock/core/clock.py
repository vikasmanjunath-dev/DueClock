"""Statutory-clock engine: pure functions, no I/O, no LLM.

Rules implemented (validation report; MSMED Act ss.15-16, Income Tax Act s.43B(h)):
- Covered sellers are Udyam MICRO or SMALL manufacturers or service providers. Traders are not covered.
- Due date = delivery date (or invoice date) + 15 days, or + agreed days capped at 45 with a written agreement.
- Interest = 3x the bank rate, compounded monthly (30-day rests), pro-rata for the remaining days.

Open questions are kept as review flags, never silently decided (see QA Plan E11, E12):
- acceptance date vs delivery date: ignored unless ``use_acceptance_date=True`` (flag ACCEPTANCE_DATE_IGNORED)
- Udyam registration date after the supply date: flag REGISTERED_AFTER_SUPPLY (still computed)
- rate policy: AT_DUE_DATE (default) or THROUGH_PERIOD
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from enum import Enum
from typing import List, Optional

from .rates import RateTable

ENGINE_VERSION = "clock-1.0.0"
INTEREST_MULTIPLIER = 3
NO_AGREEMENT_DAYS = 15
MAX_AGREEMENT_DAYS = 45
NOT_COVERED_DEFAULT_DAYS = 30


class Coverage(str, Enum):
    COVERED = "COVERED"
    NOT_COVERED_TRADER = "NOT_COVERED_TRADER"
    NOT_COVERED_SIZE = "NOT_COVERED_SIZE"
    UNKNOWN = "UNKNOWN"


class RatePolicy(str, Enum):
    AT_DUE_DATE = "at_due_date"
    THROUGH_PERIOD = "through_period"


@dataclass(frozen=True)
class SupplierFacts:
    category: str                      # MICRO, SMALL, MEDIUM, LARGE, UNKNOWN
    activity: str                      # MANUFACTURING, SERVICES, TRADING, UNKNOWN
    udyam_registration_date: Optional[date] = None


@dataclass(frozen=True)
class InvoiceFacts:
    invoice_date: date
    outstanding: float
    delivery_date: Optional[date] = None
    acceptance_date: Optional[date] = None
    has_written_agreement: bool = False
    agreed_credit_days: int = 0


@dataclass(frozen=True)
class ClockResult:
    coverage: str
    covered: bool
    base_date: date
    due_date: date
    statutory_cap_days: Optional[int]
    effective_days: int
    days_remaining: int
    overdue_days: int
    is_overdue: bool
    outstanding: float
    annual_interest_rate_pct: float
    interest: float
    disallowance_exposure: bool
    financial_year: str
    review_flags: List[str] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)
    engine_version: str = ENGINE_VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("base_date", "due_date"):
            d[k] = d[k].isoformat()
        return d


def assess_coverage(supplier: SupplierFacts) -> Coverage:
    cat, act = (supplier.category or "UNKNOWN").upper(), (supplier.activity or "UNKNOWN").upper()
    if act == "TRADING":
        return Coverage.NOT_COVERED_TRADER
    if cat in ("MEDIUM", "LARGE"):
        return Coverage.NOT_COVERED_SIZE
    if cat in ("MICRO", "SMALL") and act in ("MANUFACTURING", "SERVICES"):
        return Coverage.COVERED
    return Coverage.UNKNOWN


def compound_interest(principal: float, overdue_days: int, annual_rate_pct: float) -> float:
    """Monthly rests: each full 30-day month compounds; the remainder days accrue pro-rata."""
    if principal <= 0 or overdue_days <= 0:
        return 0.0
    mr = annual_rate_pct / 100.0 / 12.0
    full, rem = divmod(overdue_days, 30)
    amount = principal * (1.0 + mr) ** full
    if rem:
        amount *= 1.0 + mr * rem / 30.0
    return round(amount - principal, 2)


def _interest_through_period(principal: float, due: date, overdue_days: int, rates: RateTable) -> float:
    full, rem = divmod(overdue_days, 30)
    amount = principal
    for k in range(full):
        mr = rates.rate_on(due + timedelta(days=30 * k)) * INTEREST_MULTIPLIER / 100.0 / 12.0
        amount *= 1.0 + mr
    if rem:
        mr = rates.rate_on(due + timedelta(days=30 * full)) * INTEREST_MULTIPLIER / 100.0 / 12.0
        amount *= 1.0 + mr * rem / 30.0
    return round(amount - principal, 2)


def financial_year(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"FY {y}-{str(y + 1)[-2:]}"


def financial_year_end(d: date) -> date:
    return date(d.year + 1, 3, 31) if d.month >= 4 else date(d.year, 3, 31)


def evaluate(
    invoice: InvoiceFacts,
    supplier: SupplierFacts,
    as_of: date,
    rates: Optional[RateTable] = None,
    *,
    rate_policy: RatePolicy = RatePolicy.AT_DUE_DATE,
    use_acceptance_date: bool = False,
) -> ClockResult:
    rates = rates or RateTable.default()
    flags: List[str] = []
    trace: List[str] = []

    base = invoice.delivery_date or invoice.invoice_date
    trace.append(f"base date = {'delivery' if invoice.delivery_date else 'invoice'} date {base.isoformat()}")
    if invoice.acceptance_date and invoice.acceptance_date != base:
        if use_acceptance_date:
            base = invoice.acceptance_date
            trace.append(f"acceptance date used: {base.isoformat()}")
        else:
            flags.append("ACCEPTANCE_DATE_IGNORED")
            trace.append("acceptance date differs from delivery; ignored until the rule is confirmed")

    coverage = assess_coverage(supplier)
    covered = coverage == Coverage.COVERED
    if coverage == Coverage.UNKNOWN:
        flags.append("SUPPLIER_PROFILE_INCOMPLETE")
    if covered:
        if supplier.udyam_registration_date is None:
            flags.append("REGISTRATION_DATE_MISSING")
        elif supplier.udyam_registration_date > base:
            flags.append("REGISTERED_AFTER_SUPPLY")

    cap: Optional[int]
    if covered:
        if invoice.has_written_agreement:
            cap = MAX_AGREEMENT_DAYS
            if invoice.agreed_credit_days <= 0:
                days = NO_AGREEMENT_DAYS
                flags.append("AGREEMENT_DAYS_INVALID")
                trace.append("written agreement without valid days: 15-day rule applied")
            elif invoice.agreed_credit_days > MAX_AGREEMENT_DAYS:
                days = MAX_AGREEMENT_DAYS
                flags.append("AGREEMENT_CAPPED")
                trace.append(f"agreed {invoice.agreed_credit_days} days capped at {MAX_AGREEMENT_DAYS} (MSMED s.15)")
            else:
                days = invoice.agreed_credit_days
                trace.append(f"written agreement: {days} days (cap {MAX_AGREEMENT_DAYS})")
        else:
            cap, days = NO_AGREEMENT_DAYS, NO_AGREEMENT_DAYS
            trace.append("no written agreement: 15-day rule (MSMED s.15)")
    else:
        cap = None
        days = invoice.agreed_credit_days if invoice.agreed_credit_days > 0 else NOT_COVERED_DEFAULT_DAYS
        trace.append(f"not covered ({coverage.value}): agreed terms of {days} days, no statutory clock")

    due = base + timedelta(days=days)
    remaining = (due - as_of).days
    outstanding = max(0.0, round(invoice.outstanding, 2))
    overdue = max(0, (as_of - due).days) if outstanding > 0 else 0
    is_overdue = overdue > 0

    rate_pct = rates.rate_on(due) * INTEREST_MULTIPLIER
    interest = 0.0
    if covered and is_overdue:
        if rate_policy == RatePolicy.THROUGH_PERIOD:
            interest = _interest_through_period(outstanding, due, overdue, rates)
        else:
            interest = compound_interest(outstanding, overdue, rate_pct)
        trace.append(f"interest {rate_pct:.2f}% p.a. ({INTEREST_MULTIPLIER}x bank rate), monthly rests, {overdue} days overdue")
    if not rates.fully_confirmed:
        flags.append("RATE_TABLE_UNCONFIRMED")
    if rates.is_stale(as_of):
        flags.append("RATE_TABLE_STALE")

    exposure = covered and is_overdue
    if exposure:
        trace.append("43B(h) exposure flagged; payment-basis rule awaits reviewer confirmation")

    return ClockResult(
        coverage=coverage.value, covered=covered, base_date=base, due_date=due, statutory_cap_days=cap,
        effective_days=days, days_remaining=remaining, overdue_days=overdue, is_overdue=is_overdue,
        outstanding=outstanding, annual_interest_rate_pct=round(rate_pct, 4), interest=interest,
        disallowance_exposure=exposure, financial_year=financial_year(as_of), review_flags=flags, trace=trace,
    )


def year_end_exposure(invoice: InvoiceFacts, supplier: SupplierFacts, any_date_in_fy: date, rates: Optional[RateTable] = None) -> ClockResult:
    """Evaluate as of the financial year end that contains ``any_date_in_fy``."""
    return evaluate(invoice, supplier, financial_year_end(any_date_in_fy), rates)
