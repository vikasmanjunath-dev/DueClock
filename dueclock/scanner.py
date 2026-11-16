"""
DueClock Year-End Section 43B(h) Exposure Scanner
Audits buyer purchase ledgers, calculates taxable income disallowance,
and auto-generates Form 3CD Clause 22 and Form MSME-1 audit schedules.
"""

from typing import List, Dict, Any, Optional
from datetime import date
from pydantic import BaseModel
from dueclock.models import EnterpriseCategory, EnterpriseActivity
from dueclock.engine import StatutoryEngine


class VendorPayableBill(BaseModel):
    bill_id: str
    bill_number: str
    vendor_name: str
    vendor_pan: Optional[str] = None
    vendor_gstin: Optional[str] = None
    udyam_reg_number: Optional[str] = None
    enterprise_category: EnterpriseCategory = EnterpriseCategory.MICRO
    enterprise_activity: EnterpriseActivity = EnterpriseActivity.MANUFACTURING
    bill_date: date
    bill_amount: float
    paid_amount: float = 0.0
    payment_date: Optional[date] = None
    has_written_agreement: bool = False
    agreed_credit_days: int = 15

    @property
    def outstanding_amount(self) -> float:
        return max(0.0, round(self.bill_amount - self.paid_amount, 2))


class ExposureAuditResult(BaseModel):
    total_bills_audited: int
    total_vendors_count: int
    micro_small_vendors_count: int
    total_payable_amount: float
    total_overdue_msme_amount: float
    section_43bh_disallowed_amount: float
    estimated_additional_tax_liability: float  # at 25% or 30% corporate rate
    total_accrued_msmed_interest: float
    audit_date: date
    form_3cd_clause_22: Dict[str, Any]
    form_msme_1_entries: List[Dict[str, Any]]
    detailed_bills: List[Dict[str, Any]]


class Section43bScanner:
    """
    Auditing engine for Section 43B(h) Income Tax disallowances.
    Provides CAs and CFOs with an immediate calculation of tax exposure.
    """

    def __init__(self, corporate_tax_rate: float = 0.25168): # 22% + 10% surcharge + 4% cess = 25.168%
        self.engine = StatutoryEngine()
        self.corporate_tax_rate = corporate_tax_rate

    def scan_purchase_ledger(
        self,
        bills: List[VendorPayableBill],
        as_of_date: Optional[date] = None
    ) -> ExposureAuditResult:
        if as_of_date is None:
            as_of_date = date.today()

        total_payable = 0.0
        total_overdue = 0.0
        disallowed_43bh = 0.0
        total_interest = 0.0

        vendor_set = set()
        msme_vendor_set = set()
        detailed_list: List[Dict[str, Any]] = []
        msme_1_list: List[Dict[str, Any]] = []

        for b in bills:
            vendor_set.add(b.vendor_name)
            total_payable += b.outstanding_amount

            is_covered = (
                b.enterprise_category in [EnterpriseCategory.MICRO, EnterpriseCategory.SMALL]
                and b.enterprise_activity in [EnterpriseActivity.MANUFACTURING, EnterpriseActivity.SERVICES]
                and bool(b.udyam_reg_number)
            )

            if is_covered:
                msme_vendor_set.add(b.vendor_name)

                # Statutory cap
                allowed_days = min(b.agreed_credit_days, 45) if b.has_written_agreement else 15
                due_date = b.bill_date + __import__("datetime").timedelta(days=allowed_days)

                is_overdue = False
                overdue_days = 0

                # Check if settled or pending as of audit date
                settle_date = b.payment_date or as_of_date
                if settle_date > due_date:
                    is_overdue = True
                    overdue_days = (settle_date - due_date).days

                accrued_int = self.engine.calculate_compound_interest(b.outstanding_amount, overdue_days)

                # 43B(h) disallowance applies if unpaid as of March 31 beyond due date
                disallowed = False
                if b.outstanding_amount > 0 and is_overdue:
                    disallowed = True
                    disallowed_43bh += b.outstanding_amount
                    total_overdue += b.outstanding_amount

                total_interest += accrued_int

                item = {
                    "bill_number": b.bill_number,
                    "vendor_name": b.vendor_name,
                    "udyam_reg_number": b.udyam_reg_number,
                    "bill_date": b.bill_date.isoformat(),
                    "due_date": due_date.isoformat(),
                    "bill_amount": b.bill_amount,
                    "outstanding_amount": b.outstanding_amount,
                    "allowed_days": allowed_days,
                    "overdue_days": overdue_days,
                    "is_overdue": is_overdue,
                    "is_43bh_disallowed": disallowed,
                    "accrued_statutory_interest": accrued_int
                }
                detailed_list.append(item)

                if is_overdue and b.outstanding_amount > 0:
                    msme_1_list.append({
                        "supplier_name": b.vendor_name,
                        "udyam_number": b.udyam_reg_number,
                        "amount_due": b.outstanding_amount,
                        "date_from_which_due": due_date.isoformat(),
                        "reason_for_delay": "Pending invoice reconciliation"
                    })

        additional_tax = round(disallowed_43bh * self.corporate_tax_rate, 2)

        # Form 3CD Clause 22 Schedule structure per ICAI guidance
        form_3cd_clause_22 = {
            "clause_22_heading": "Amount of interest inadmissible under section 23 of the MSMED Act, 2006",
            "principal_amount_unpaid": round(disallowed_43bh, 2),
            "interest_due_unpaid": round(total_interest, 2),
            "interest_paid_during_year": 0.0,
            "interest_accrued_and_remaining_unpaid": round(total_interest, 2),
            "tax_disallowed_under_43bh": round(disallowed_43bh, 2)
        }

        return ExposureAuditResult(
            total_bills_audited=len(bills),
            total_vendors_count=len(vendor_set),
            micro_small_vendors_count=len(msme_vendor_set),
            total_payable_amount=round(total_payable, 2),
            total_overdue_msme_amount=round(total_overdue, 2),
            section_43bh_disallowed_amount=round(disallowed_43bh, 2),
            estimated_additional_tax_liability=additional_tax,
            total_accrued_msmed_interest=round(total_interest, 2),
            audit_date=as_of_date,
            form_3cd_clause_22=form_3cd_clause_22,
            form_msme_1_entries=msme_1_list,
            detailed_bills=detailed_list
        )
