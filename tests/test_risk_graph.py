"""
Unit tests for Buyer Payment Behavior Risk Graph and Samadhaan Petition Drafter
"""

import pytest
from datetime import date
from dueclock.risk_graph import BuyerRiskGraphEngine, BuyerPaymentRecord
from dueclock.samadhaan_drafter import SamadhaanPetitionDrafter
from dueclock.models import SupplierProfile, BuyerProfile, Invoice, EnterpriseCategory, EnterpriseActivity
from dueclock.engine import StatutoryEngine


def test_buyer_risk_graph_scoring():
    engine = BuyerRiskGraphEngine()

    # Prompt payer
    profile_prompt = engine.evaluate_buyer_risk("AAACB1111A", "Bangalore Auto Components Ltd")
    assert profile_prompt.total_invoices_tracked == 12
    assert profile_prompt.dueclock_reliability_score >= 80
    assert profile_prompt.treds_financier_grade in ["AAA", "AA"]

    # Chronic delinquent
    profile_late = engine.evaluate_buyer_risk("XYZAB2222K", "Zenith Tooling Systems")
    assert profile_late.total_invoices_tracked == 8
    assert profile_late.dueclock_reliability_score < 70
    assert profile_late.pct_overdue_past_day_45 > 50


def test_samadhaan_petition_generation():
    drafter = SamadhaanPetitionDrafter()
    stat_engine = StatutoryEngine()

    supplier = SupplierProfile(
        business_name="Precision Metal Components Pvt Ltd",
        gstin="29ABCDE1234F1Z5",
        udyam_reg_number="UDYAM-KR-03-0012345",
        category=EnterpriseCategory.MICRO,
        activity=EnterpriseActivity.MANUFACTURING,
        upi_vpa="precisionmetal@icici",
        phone="+919876543210"
    )
    buyer = BuyerProfile(
        buyer_name="Mega Engineering Ltd",
        gstin="29XYZAB5678K1Z2",
        pan="XYZAB5678K",
        mobile="+919844455566"
    )
    invoice = Invoice(
        id="inv_samadhaan_01",
        invoice_number="INV/2026/099",
        invoice_date=date(2026, 9, 1),
        amount=250000.0,
        buyer=buyer,
        has_written_agreement=False,
        agreed_credit_days=15
    )

    eval_res = stat_engine.evaluate_invoice(invoice, supplier, as_of_date=date(2026, 11, 1))
    petition = drafter.generate_samadhaan_petition(supplier, invoice, eval_res, as_of_date=date(2026, 11, 1))

    assert "SECTION 18(1) OF THE" in petition
    assert "UDYAM-KR-03-0012345" in petition
    assert "Mega Engineering Ltd" in petition
    assert "₹250,000.00" in petition
    assert "Section 19" in petition
    assert "75% pre-deposit" in petition
