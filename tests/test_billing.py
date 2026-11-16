"""
Unit tests for Subscription Billing & GST Invoicing Engine
"""

import pytest
from dueclock.billing import BillingEngine, PlanTier


def test_starter_subscription_billing_with_ca():
    billing = BillingEngine()
    result = billing.create_subscription(
        tenant_id="tenant_metal_01",
        customer_name="Precision Metal Components",
        plan_tier=PlanTier.STARTER,
        customer_gstin="29ABCDE1234F1Z5",
        is_interstate=False,
        referral_ca_id="ca_blr_001"
    )

    sub = result["subscription"]
    inv = result["gst_invoice"]

    assert sub["annual_fee"] == 6500.0
    assert sub["ca_commission_amount"] == 1300.0  # 20% of 6,500
    assert inv["sac_code"] == "998314"
    assert inv["cgst_rate_pct"] == 9.0
    assert inv["sgst_rate_pct"] == 9.0
    assert inv["gross_total"] == 6500.0 + (6500.0 * 0.18)  # ₹7,670.00
    assert result["payment_gateway_payload"]["amount_in_paise"] == 767000


def test_founding_pilot_discount():
    billing = BillingEngine()
    result = billing.create_subscription(
        tenant_id="tenant_pilot_01",
        customer_name="Pilot Machine Works",
        plan_tier=PlanTier.FOUNDING_PILOT
    )

    assert result["subscription"]["annual_fee"] == 3250.0
    assert result["gst_invoice"]["gross_total"] == 3250.0 + (3250.0 * 0.18)
