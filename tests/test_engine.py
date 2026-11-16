"""
Unit tests for DueClock Statutory-Clock & Section 43B(h) Engine
"""

import pytest
from datetime import date, timedelta
from dueclock.models import (
    SupplierProfile,
    BuyerProfile,
    Invoice,
    EnterpriseCategory,
    EnterpriseActivity,
    InvoiceStatus
)
from dueclock.engine import (
    StatutoryEngine,
    RBI_BANK_RATE_PERCENT,
    STATUTORY_INTEREST_ANNUAL_PCT
)


@pytest.fixture
def micro_manufacturer():
    return SupplierProfile(
        id="supp_01",
        business_name="Precision Metal Components Pvt Ltd",
        gstin="29ABCDE1234F1Z5",
        udyam_reg_number="UDYAM-KR-03-0012345",
        category=EnterpriseCategory.MICRO,
        activity=EnterpriseActivity.MANUFACTURING,
        upi_vpa="precisionmetal@icici",
        phone="+919876543210"
    )


@pytest.fixture
def wholesale_trader():
    return SupplierProfile(
        id="supp_trader",
        business_name="Bangalore Steel Distributors",
        gstin="29XYZAB5678K1Z2",
        udyam_reg_number="UDYAM-KR-03-0099999",
        category=EnterpriseCategory.SMALL,
        activity=EnterpriseActivity.TRADING,
        upi_vpa="bangaloresteel@hdfcbank",
        phone="+919811122233"
    )


@pytest.fixture
def corporate_buyer():
    return BuyerProfile(
        buyer_name="Mega Auto Ancillary Ltd",
        gstin="29AAACM1234H1Z1",
        pan="AAACM1234H",
        mobile="+919844455566"
    )


def test_15_day_cap_without_written_agreement(micro_manufacturer, corporate_buyer):
    """
    Under Section 15 MSMED Act, if there is NO written agreement,
    payment is strictly due on the 15th day (appointed day).
    """
    engine = StatutoryEngine()
    inv_date = date(2026, 11, 1)

    invoice = Invoice(
        id="inv_101",
        invoice_number="INV-2026-001",
        invoice_date=inv_date,
        delivery_date=inv_date,
        amount=100000.0,
        buyer=corporate_buyer,
        has_written_agreement=False,
        agreed_credit_days=30  # Verbal or informal claim
    )

    eval_result = engine.evaluate_invoice(invoice, micro_manufacturer, as_of_date=date(2026, 11, 10))

    assert eval_result.is_43bh_covered is True
    assert eval_result.statutory_max_allowed_days == 15
    assert eval_result.effective_days_granted == 15
    assert eval_result.statutory_due_date == inv_date + timedelta(days=15)
    assert eval_result.is_overdue is False
    assert eval_result.days_remaining == 6


def test_45_day_mandatory_ceiling_with_agreement(micro_manufacturer, corporate_buyer):
    """
    Under Section 15 MSMED Act proviso, even if the written agreement specifies
    60, 90, or 120 days, the statutory ceiling is capped strictly at 45 days.
    """
    engine = StatutoryEngine()
    inv_date = date(2026, 11, 1)

    # Buyer insisted on 90-day credit in contract
    invoice = Invoice(
        id="inv_102",
        invoice_number="INV-2026-002",
        invoice_date=inv_date,
        delivery_date=inv_date,
        amount=500000.0,
        buyer=corporate_buyer,
        has_written_agreement=True,
        agreed_credit_days=90
    )

    eval_result = engine.evaluate_invoice(invoice, micro_manufacturer, as_of_date=inv_date)

    assert eval_result.is_43bh_covered is True
    assert eval_result.statutory_max_allowed_days == 45
    assert eval_result.effective_days_granted == 45
    assert eval_result.statutory_due_date == inv_date + timedelta(days=45)


def test_trader_exclusion_rule(wholesale_trader, corporate_buyer):
    """
    Wholesale & Retail traders are excluded from Section 43B(h)
    and MSMED statutory delayed-payment penalties.
    """
    engine = StatutoryEngine()
    inv_date = date(2026, 11, 1)

    invoice = Invoice(
        id="inv_trader",
        invoice_number="INV-TR-001",
        invoice_date=inv_date,
        amount=250000.0,
        buyer=corporate_buyer,
        has_written_agreement=True,
        agreed_credit_days=60
    )

    eval_result = engine.evaluate_invoice(invoice, wholesale_trader, as_of_date=inv_date)

    assert eval_result.is_43bh_covered is False
    assert eval_result.annual_interest_rate_pct == 0.0
    assert "Trader exclusion applies" in eval_result.legal_summary


def test_compound_interest_calculation(micro_manufacturer, corporate_buyer):
    """
    Overdue invoices accrue compound interest with monthly rests at 3x the RBI bank rate (~19.5%).
    """
    engine = StatutoryEngine()
    inv_date = date(2026, 10, 1)

    # 15 days credit -> due on Oct 16
    invoice = Invoice(
        id="inv_overdue",
        invoice_number="INV-OD-001",
        invoice_date=inv_date,
        amount=100000.0,
        buyer=corporate_buyer,
        has_written_agreement=False,
        agreed_credit_days=15
    )

    # Evaluate 30 days overdue (Nov 15)
    as_of = inv_date + timedelta(days=45)  # Oct 1 + 45 days = Nov 15 (30 days overdue)
    eval_result = engine.evaluate_invoice(invoice, micro_manufacturer, as_of_date=as_of)

    assert eval_result.is_overdue is True
    assert eval_result.annual_interest_rate_pct == 19.50
    assert eval_result.accrued_statutory_interest > 0.0

    # 1 month of compound interest at 19.5% p.a. on 100,000 should be ~1,625
    expected_approx = 100000.0 * (0.195 / 12.0)
    assert abs(eval_result.accrued_statutory_interest - expected_approx) < 10.0
