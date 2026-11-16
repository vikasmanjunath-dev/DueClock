"""
DueClock Subscription Mandate Billing & Referral Payout Engine
Handles Razorpay/Cashfree recurring subscription plans, SAC 998314 GST invoice generation,
and computes 20% CA partner referral commission payouts.
"""

from typing import Dict, Any, List, Optional
from datetime import date, datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field


class PlanTier(str, Enum):
    FOUNDING_PILOT = "FOUNDING_PILOT"  # ₹3,250 / year (50% early pilot discount)
    STARTER = "STARTER"                # ₹6,500 / year
    GROWTH = "GROWTH"                  # ₹24,000 / year
    PRO = "PRO"                        # ₹60,000 / year


class GSTInvoice(BaseModel):
    invoice_number: str
    invoice_date: date
    sac_code: str = "998314"           # IT Software Services SAC Code
    customer_name: str
    customer_gstin: Optional[str] = None
    customer_pan: Optional[str] = None
    plan_tier: PlanTier
    base_amount: float
    cgst_rate_pct: float = 9.0
    sgst_rate_pct: float = 9.0
    igst_rate_pct: float = 0.0         # Set to 18% if inter-state
    total_tax_amount: float
    gross_total: float
    currency: str = "INR"
    status: str = "PAID"


class SubscriptionContract(BaseModel):
    id: str
    tenant_id: str
    plan_tier: PlanTier
    start_date: date
    next_renewal_date: date
    annual_fee: float
    is_active: bool = True
    mandate_token: Optional[str] = None
    referral_ca_id: Optional[str] = None
    ca_commission_amount: float = 0.0


class BillingEngine:
    """
    Manages customer subscription tiers, recurring billing, GST invoices, and partner commissions.
    """

    PLAN_PRICING: Dict[PlanTier, float] = {
        PlanTier.FOUNDING_PILOT: 3250.0,
        PlanTier.STARTER: 6500.0,
        PlanTier.GROWTH: 24000.0,
        PlanTier.PRO: 60000.0
    }

    def __init__(self, dueclock_gstin: str = "29AABCD1234E1Z9"):
        self.dueclock_gstin = dueclock_gstin
        self.subscriptions: Dict[str, SubscriptionContract] = {}
        self.invoices: List[GSTInvoice] = []

    def create_subscription(
        self,
        tenant_id: str,
        customer_name: str,
        plan_tier: PlanTier = PlanTier.STARTER,
        customer_gstin: Optional[str] = None,
        is_interstate: bool = False,
        referral_ca_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Creates an annual subscription, applies GST, awards 20% CA commission, and returns tax invoice."""
        base_price = self.PLAN_PRICING[plan_tier]

        if is_interstate:
            cgst = 0.0
            sgst = 0.0
            igst = round(base_price * 0.18, 2)
            total_tax = igst
        else:
            cgst = round(base_price * 0.09, 2)
            sgst = round(base_price * 0.09, 2)
            igst = 0.0
            total_tax = cgst + sgst

        gross_amt = round(base_price + total_tax, 2)

        # 20% CA referral commission
        ca_commission = round(base_price * 0.20, 2) if referral_ca_id else 0.0

        today = date.today()
        renewal = today.replace(year=today.year + 1)

        sub = SubscriptionContract(
            id=f"sub_{tenant_id}_{int(datetime.now().timestamp())}",
            tenant_id=tenant_id,
            plan_tier=plan_tier,
            start_date=today,
            next_renewal_date=renewal,
            annual_fee=base_price,
            referral_ca_id=referral_ca_id,
            ca_commission_amount=ca_commission
        )
        self.subscriptions[sub.id] = sub

        gst_inv = GSTInvoice(
            invoice_number=f"DC-INV-{len(self.invoices) + 1:04d}",
            invoice_date=today,
            customer_name=customer_name,
            customer_gstin=customer_gstin,
            plan_tier=plan_tier,
            base_amount=base_price,
            cgst_rate_pct=0.0 if is_interstate else 9.0,
            sgst_rate_pct=0.0 if is_interstate else 9.0,
            igst_rate_pct=18.0 if is_interstate else 0.0,
            total_tax_amount=total_tax,
            gross_total=gross_amt
        )
        self.invoices.append(gst_inv)

        return {
            "subscription": sub.model_dump(),
            "gst_invoice": gst_inv.model_dump(),
            "payment_gateway_payload": {
                "amount_in_paise": int(gross_amt * 100),
                "currency": "INR",
                "notes": {
                    "tenant_id": tenant_id,
                    "plan": plan_tier.value,
                    "referral_ca": referral_ca_id or "NONE"
                }
            }
        }
