"""
DueClock Domain Models
Pydantic schemas and data classes for the Statutory-Clock Autopilot.
"""

from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import date, datetime, timezone
from pydantic import BaseModel, Field


class EnterpriseCategory(str, Enum):
    MICRO = "MICRO"
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"
    UNKNOWN = "UNKNOWN"


class EnterpriseActivity(str, Enum):
    MANUFACTURING = "MANUFACTURING"
    SERVICES = "SERVICES"
    TRADING = "TRADING"  # Retail or Wholesale trader - EXCLUDED from 43B(h)
    UNKNOWN = "UNKNOWN"


class InvoiceStatus(str, Enum):
    PENDING = "PENDING"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    DISPUTED = "DISPUTED"
    WRITTEN_OFF = "WRITTEN_OFF"


class CadenceStage(str, Enum):
    STAGE_MINUS_5 = "STAGE_MINUS_5"  # T-5 days: Polite advance notice
    STAGE_0 = "STAGE_0"              # T-0: Due date today + UPI link
    STAGE_PLUS_5 = "STAGE_PLUS_5"    # T+5: Statutory MSMED clock + 3x interest notice
    STAGE_PLUS_15 = "STAGE_PLUS_15"  # T+15: Section 43B(h) tax disallowance warning
    STAGE_PLUS_30 = "STAGE_PLUS_30"  # T+30: Pre-Samadhaan statutory recovery notice


class SupportedLanguage(str, Enum):
    ENGLISH = "en"
    HINDI = "hi"
    KANNADA = "kn"


class SupplierProfile(BaseModel):
    id: str = "tenant_default"
    business_name: str
    trade_name: Optional[str] = None
    gstin: str
    udyam_reg_number: str
    category: EnterpriseCategory = EnterpriseCategory.MICRO
    activity: EnterpriseActivity = EnterpriseActivity.MANUFACTURING
    upi_vpa: str  # Direct payments route here without custody
    phone: str
    email: Optional[str] = None
    address: Optional[str] = None

    @property
    def is_eligible_43bh(self) -> bool:
        """Section 43B(h) strictly applies only to Micro and Small Manufacturers & Services."""
        return (
            self.category in [EnterpriseCategory.MICRO, EnterpriseCategory.SMALL]
            and self.activity in [EnterpriseActivity.MANUFACTURING, EnterpriseActivity.SERVICES]
        )


class BuyerProfile(BaseModel):
    id: Optional[str] = None
    buyer_name: str
    gstin: Optional[str] = None
    pan: Optional[str] = None
    mobile: str
    email: Optional[str] = None
    contact_person: Optional[str] = None


class Invoice(BaseModel):
    id: str
    invoice_number: str
    invoice_date: date
    delivery_date: Optional[date] = None  # Date of acceptance of goods
    amount: float
    paid_amount: float = 0.0
    buyer: BuyerProfile
    has_written_agreement: bool = False
    agreed_credit_days: int = 15          # Default 15 days under MSMED Act
    tally_voucher_type: Optional[str] = "Sales"
    tally_guid: Optional[str] = None
    status: InvoiceStatus = InvoiceStatus.PENDING
    promised_payment_date: Optional[date] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def outstanding_amount(self) -> float:
        return max(0.0, round(self.amount - self.paid_amount, 2))


class StatutoryEvaluation(BaseModel):
    invoice_id: str
    invoice_number: str
    statutory_due_date: date
    statutory_max_allowed_days: int       # 15 or 45 days
    effective_days_granted: int
    days_elapsed: int
    days_remaining: int
    is_overdue: bool
    is_43bh_covered: bool
    applicable_statute: str
    annual_interest_rate_pct: float       # 3x RBI Bank Rate (~19.5%)
    accrued_statutory_interest: float     # Compound interest under Sec 16
    tax_disallowance_risk: bool           # True if crosses FY without payment
    assessment_year: str
    legal_summary: str


class BankCreditEntry(BaseModel):
    id: str
    transaction_date: date
    amount: float
    utr_number: Optional[str] = None
    narration: str
    payer_vpa: Optional[str] = None
    matched_invoice_id: Optional[str] = None
    confidence_score: float = 0.0
