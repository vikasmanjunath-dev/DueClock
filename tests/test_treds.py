"""
Unit tests for TReDS Invoice Financing Pre-Screening and WhatsApp BSP Adapter
"""

import pytest
from datetime import date
from dueclock.treds_referral import TReDSReferralEngine
from dueclock.bsp_adapter import WhatsAppBSPAdapter, MessageDeliveryStatus
from dueclock.models import SupplierProfile, BuyerProfile, Invoice, EnterpriseCategory, EnterpriseActivity


def test_treds_eligibility_mandated_buyer():
    engine = TReDSReferralEngine()

    supplier = SupplierProfile(
        business_name="Precision Metal Components Pvt Ltd",
        gstin="29ABCDE1234F1Z5",
        udyam_reg_number="UDYAM-KR-03-0012345",
        category=EnterpriseCategory.MICRO,
        activity=EnterpriseActivity.MANUFACTURING,
        upi_vpa="precisionmetal@icici",
        phone="+919876543210"
    )

    # Buyer has >250 Cr turnover
    buyer = BuyerProfile(
        buyer_name="Bangalore Auto Components Ltd",
        gstin="29AAACB1111A1Z1",
        pan="AAACB1111A",
        mobile="+919845012345"
    )

    invoice = Invoice(
        id="inv_treds_01",
        invoice_number="INV/2026/041",
        invoice_date=date(2026, 11, 1),
        amount=1000000.0,  # ₹10 Lakhs
        buyer=buyer,
        has_written_agreement=True,
        agreed_credit_days=45
    )

    res = engine.evaluate_invoice_for_treds(invoice, supplier)

    assert res.is_eligible_for_treds is True
    assert res.eligibility_grade == "TReDS_READY"
    assert res.buyer_turnover_exceeds_250cr is True
    # 0.4% referral fee on 1,000,000 = 4,000
    assert res.dueclock_referral_commission_fee == 4000.0
    assert res.estimated_upfront_payout_amount > 980000.0


def test_treds_ineligibility_trader():
    engine = TReDSReferralEngine()

    trader_supplier = SupplierProfile(
        business_name="Bangalore Traders",
        gstin="29ABCDE1234F1Z5",
        udyam_reg_number="UDYAM-KR-03-0099999",
        category=EnterpriseCategory.SMALL,
        activity=EnterpriseActivity.TRADING,
        upi_vpa="trader@upi",
        phone="+919876543210"
    )

    buyer = BuyerProfile(
        buyer_name="Bangalore Auto Components Ltd",
        pan="AAACB1111A",
        mobile="+919845012345"
    )

    invoice = Invoice(
        id="inv_trader_treds",
        invoice_number="INV/2026/042",
        invoice_date=date(2026, 11, 1),
        amount=500000.0,
        buyer=buyer
    )

    res = engine.evaluate_invoice_for_treds(invoice, trader_supplier)
    assert res.is_eligible_for_treds is False
    assert any("traders excluded" in r for r in res.reasons)


def test_whatsapp_bsp_mock_dispatch():
    bsp = WhatsAppBSPAdapter(use_mock=True)

    record = bsp.dispatch_utility_reminder(
        recipient_mobile="+919845012345",
        template_name="dueclock_utility_t0",
        language_code="en",
        parameters=["Bangalore Auto", "INV/2026/041", "1,45,000.00", "07-Nov-2026", "upi://pay..."]
    )

    assert record.delivery_status == MessageDeliveryStatus.DELIVERED
    assert record.cost_in_inr == 0.145
    assert record.bsp_vendor == "MOCK_SANDBOX"
    assert len(bsp.dispatch_log) == 1
