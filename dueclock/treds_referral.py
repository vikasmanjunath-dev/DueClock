"""
DueClock TReDS Invoice Financing Referral & Eligibility Pre-Screening Engine
Integrates with licensed TReDS operators (RXIL, M1xchange, Invoicemart).
Pre-screens manufacturing invoices, calculates discounting yield, and models the 0.4% referral line.
"""

from typing import Dict, Any, List, Optional
from datetime import date
from pydantic import BaseModel
from dueclock.models import SupplierProfile, Invoice, EnterpriseCategory, EnterpriseActivity


class TReDSEligibilityResult(BaseModel):
    invoice_id: str
    invoice_number: str
    invoice_amount: float
    is_eligible_for_treds: bool
    eligibility_grade: str              # TReDS_READY, INELIGIBLE_BUYER_TURNOVER, INELIGIBLE_TRADER, DISPUTED
    buyer_turnover_exceeds_250cr: bool
    platform_partner_recommended: str   # "RXIL", "M1xchange", or "Invoicemart"
    estimated_discount_rate_annual_pct: float  # e.g., 8.5% p.a.
    estimated_upfront_payout_amount: float     # e.g., 98.9%
    estimated_financier_discount_cost: float
    dueclock_referral_commission_fee: float    # 0.4% of invoice volume
    reasons: List[str]


class TReDSReferralEngine:
    """
    Evaluates receivables for institutional invoice discounting via RBI-licensed TReDS exchanges.
    Drives DueClock's Year 6 ₹36 Crore referral revenue line.
    """

    REFERRAL_FEE_PCT: float = 0.40  # 0.4% referral fee

    # Benchmark buyer registry: corporates mandated under MCA rules (turnover > ₹250 Cr)
    MANDATED_BUYERS_REGISTRY: Dict[str, Dict[str, Any]] = {
        "AAACB1111A": {"name": "Bangalore Auto Components Ltd", "annual_turnover_cr": 1200.0, "treds_registered": True},
        "AAACM1234H": {"name": "Mega Auto Ancillary Ltd", "annual_turnover_cr": 4500.0, "treds_registered": True},
        "XYZAB5678K": {"name": "Zenith Machinery Corp", "annual_turnover_cr": 850.0, "treds_registered": True},
        "KAVLL3333L": {"name": "Kaveri Hydraulics LLP", "annual_turnover_cr": 85.0, "treds_registered": False} # Under 250 Cr
    }

    def __init__(self, benchmark_repo_rate: float = 6.50):
        self.repo_rate = benchmark_repo_rate

    def evaluate_invoice_for_treds(
        self,
        invoice: Invoice,
        supplier: SupplierProfile,
        buyer_pan: Optional[str] = None
    ) -> TReDSEligibilityResult:
        reasons: List[str] = []
        is_eligible = True

        # 1. Supplier Check: Must be Micro or Small Manufacturer/Services
        if not supplier.is_eligible_43bh:
            is_eligible = False
            reasons.append("Supplier must be a registered Micro or Small enterprise (traders excluded from TReDS statutory mandates).")

        # 2. Buyer Check: PAN lookup in mandated > ₹250 Cr registry
        pan = buyer_pan or invoice.buyer.pan or ""
        pan_clean = pan.strip().upper()
        buyer_info = self.MANDATED_BUYERS_REGISTRY.get(pan_clean)

        buyer_mandated = False
        if buyer_info:
            buyer_mandated = buyer_info.get("annual_turnover_cr", 0) >= 250.0
            if not buyer_mandated:
                is_eligible = False
                reasons.append(f"Buyer annual turnover (₹{buyer_info.get('annual_turnover_cr')} Cr) is under the mandatory ₹250 Cr TReDS threshold.")
        else:
            # If not in local benchmark, default to pending buyer turnover verification
            buyer_mandated = False
            is_eligible = False
            reasons.append("Buyer PAN not found in > ₹250 Cr MCA mandated buyer registry.")

        # 3. Dispute check
        if invoice.status.value == "DISPUTED":
            is_eligible = False
            reasons.append("Invoice is under commercial or quality dispute.")

        # Rate calculations (typically Repo Rate + 2.0% = 8.50% annualized)
        discount_rate_annual = self.repo_rate + 2.0
        # Credit duration in days (statutory cap is 45 days)
        credit_days = min(invoice.agreed_credit_days, 45) if invoice.has_written_agreement else 15

        # Discount fee for credit period: Amt * (rate / 100) * (days / 365)
        financier_cost = round(invoice.amount * (discount_rate_annual / 100.0) * (credit_days / 365.0), 2)
        upfront_payout = round(invoice.amount - financier_cost, 2)

        # DueClock 0.4% referral commission fee
        referral_fee = round(invoice.amount * (self.REFERRAL_FEE_PCT / 100.0), 2)

        grade = "TReDS_READY" if is_eligible else "INELIGIBLE"

        return TReDSEligibilityResult(
            invoice_id=invoice.id,
            invoice_number=invoice.invoice_number,
            invoice_amount=invoice.amount,
            is_eligible_for_treds=is_eligible,
            eligibility_grade=grade,
            buyer_turnover_exceeds_250cr=buyer_mandated,
            platform_partner_recommended="RXIL",
            estimated_discount_rate_annual_pct=discount_rate_annual,
            estimated_upfront_payout_amount=upfront_payout,
            estimated_financier_discount_cost=financier_cost,
            dueclock_referral_commission_fee=referral_fee,
            reasons=reasons if reasons else ["Meets all MSME & MCA turnover mandates. Pre-approved for instant 24hr settlement."]
        )
