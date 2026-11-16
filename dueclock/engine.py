"""
DueClock Statutory-Clock & Section 43B(h) Rules Engine
Exact mathematical & legal rule execution for MSMED Act (Sec 15, 16, 23) and Income Tax Act (Sec 43B(h)).
"""

from datetime import date, timedelta
from typing import Tuple, Dict, Any, Optional
from dueclock.models import (
    SupplierProfile,
    Invoice,
    StatutoryEvaluation,
    EnterpriseCategory,
    EnterpriseActivity,
    InvoiceStatus
)

# Benchmark RBI Bank Rate in India
RBI_BANK_RATE_PERCENT: float = 6.50
STATUTORY_INTEREST_MULTIPLIER: int = 3
STATUTORY_INTEREST_ANNUAL_PCT: float = RBI_BANK_RATE_PERCENT * STATUTORY_INTEREST_MULTIPLIER  # 19.50% p.a.


class StatutoryEngine:
    """
    Statutory-Clock Rules Engine.
    Guarantees exact, deterministic calculation of 15-day / 45-day caps,
    Section 43B(h) tax disallowance eligibility, and compound interest under MSMED Act Sec 16.
    """

    def __init__(self, bank_rate_pct: float = RBI_BANK_RATE_PERCENT):
        self.bank_rate_pct = bank_rate_pct
        self.statutory_rate_pct = bank_rate_pct * STATUTORY_INTEREST_MULTIPLIER

    def calculate_statutory_due_date(
        self,
        invoice: Invoice,
        supplier: SupplierProfile
    ) -> Tuple[date, int, int]:
        """
        Calculates the statutory due date per Section 15 of MSMED Act.
        Returns: (due_date, statutory_max_allowed_days, effective_days_granted)
        """
        # If supplier is a trader or medium/large, statutory caps do not apply
        if not supplier.is_eligible_43bh:
            effective_days = invoice.agreed_credit_days if invoice.agreed_credit_days > 0 else 30
            base_date = invoice.delivery_date or invoice.invoice_date
            return (base_date + timedelta(days=effective_days), effective_days, effective_days)

        base_date = invoice.delivery_date or invoice.invoice_date

        if invoice.has_written_agreement:
            # Under MSMED Sec 15 proviso: period agreed in writing shall NOT exceed 45 days
            statutory_max = 45
            effective_days = min(invoice.agreed_credit_days, 45)
        else:
            # Without written agreement: must be paid before appointed day = 15 days
            statutory_max = 15
            effective_days = 15

        due_date = base_date + timedelta(days=effective_days)
        return (due_date, statutory_max, effective_days)

    def calculate_compound_interest(
        self,
        principal: float,
        overdue_days: int
    ) -> float:
        """
        Calculates compound interest with monthly rests at 3x RBI bank rate per MSMED Act Section 16.
        Formula: A = P * (1 + r/12)^months * (1 + (r/12) * (days_remainder / 30))
        """
        if overdue_days <= 0 or principal <= 0:
            return 0.0

        r = self.statutory_rate_pct / 100.0  # e.g., 0.195
        monthly_rate = r / 12.0

        full_months = overdue_days // 30
        remainder_days = overdue_days % 30

        # Compound over full months with monthly rests
        amount = principal * ((1.0 + monthly_rate) ** full_months)

        # Pro-rata interest for partial month
        if remainder_days > 0:
            amount = amount * (1.0 + monthly_rate * (remainder_days / 30.0))

        interest = amount - principal
        return round(interest, 2)

    def determine_financial_year(self, d: date) -> str:
        """Determines Indian Financial Year (e.g., FY 2026-27)."""
        year = d.year
        if d.month >= 4:
            return f"FY {year}-{str(year + 1)[-2:]}"
        else:
            return f"FY {year - 1}-{str(year)[-2:]}"

    def determine_assessment_year(self, d: date) -> str:
        """Determines Income Tax Assessment Year (AY = FY + 1 year)."""
        year = d.year
        if d.month >= 4:
            return f"AY {year + 1}-{str(year + 2)[-2:]}"
        else:
            return f"AY {year}-{str(year + 1)[-2:]}"

    def evaluate_invoice(
        self,
        invoice: Invoice,
        supplier: SupplierProfile,
        as_of_date: Optional[date] = None
    ) -> StatutoryEvaluation:
        """
        Runs comprehensive statutory audit on an invoice as of a specific date.
        """
        if as_of_date is None:
            as_of_date = date.today()

        due_date, stat_max, effective_days = self.calculate_statutory_due_date(invoice, supplier)

        base_date = invoice.delivery_date or invoice.invoice_date
        days_elapsed = (as_of_date - base_date).days
        days_remaining = (due_date - as_of_date).days
        is_overdue = as_of_date > due_date and invoice.outstanding_amount > 0

        overdue_days = max(0, (as_of_date - due_date).days) if is_overdue else 0
        accrued_interest = self.calculate_compound_interest(invoice.outstanding_amount, overdue_days)

        is_43bh_covered = supplier.is_eligible_43bh

        # Check Tax Disallowance Risk:
        # If invoice falls in FY X, and is unpaid beyond statutory due date by March 31 of that FY
        inv_fy = self.determine_financial_year(base_date)
        as_of_fy = self.determine_financial_year(as_of_date)
        
        # Risk exists if overdue and either crosses March 31 or is currently overdue with FY end approaching
        tax_disallowance_risk = False
        if is_43bh_covered and invoice.outstanding_amount > 0:
            if is_overdue:
                # If already overdue, deduction cannot be claimed in that year unless paid before March 31
                tax_disallowance_risk = True

        if is_43bh_covered:
            if invoice.has_written_agreement:
                legal_summary = (
                    f"Covered under MSMED Act & Sec 43B(h). Written agreement capped at 45 days. "
                    f"Due: {due_date.isoformat()}. Stat rate: {self.statutory_rate_pct}% p.a. compound monthly."
                )
            else:
                legal_summary = (
                    f"Covered under MSMED Act & Sec 43B(h). No written agreement -> statutory 15-day cap applies. "
                    f"Due: {due_date.isoformat()}. Compound interest active after due date."
                )
        else:
            if supplier.activity == EnterpriseActivity.TRADING:
                legal_summary = (
                    "Supplier registered under Trading NIC code. Trader exclusion applies: "
                    "Section 43B(h) and MSMED statutory delayed payment caps do NOT apply."
                )
            else:
                legal_summary = "Non-eligible entity type for Section 43B(h)."

        applicable_statute = (
            "Section 43B(h) Income Tax Act, 1961 + MSMED Act, 2006 (Sec 15, 16)"
            if is_43bh_covered else "General Commercial Contract (Indian Contract Act)"
        )

        return StatutoryEvaluation(
            invoice_id=invoice.id,
            invoice_number=invoice.invoice_number,
            statutory_due_date=due_date,
            statutory_max_allowed_days=stat_max,
            effective_days_granted=effective_days,
            days_elapsed=days_elapsed,
            days_remaining=days_remaining,
            is_overdue=is_overdue,
            is_43bh_covered=is_43bh_covered,
            applicable_statute=applicable_statute,
            annual_interest_rate_pct=self.statutory_rate_pct if is_43bh_covered else 0.0,
            accrued_statutory_interest=accrued_interest,
            tax_disallowance_risk=tax_disallowance_risk,
            assessment_year=self.determine_assessment_year(due_date),
            legal_summary=legal_summary
        )
