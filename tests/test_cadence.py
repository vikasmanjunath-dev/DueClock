"""
Tests for WhatsApp Cadence, UPI Link, and Reconciliation
"""

import pytest
from datetime import date
from dueclock.models import (
    SupplierProfile,
    BuyerProfile,
    Invoice,
    EnterpriseCategory,
    EnterpriseActivity,
    CadenceStage,
    SupportedLanguage,
    BankCreditEntry
)
from dueclock.engine import StatutoryEngine
from dueclock.cadence import WhatsAppCadenceManager, UPILinkGenerator
from dueclock.recon import ReconciliationEngine
from dueclock.scanner import Section43bScanner, VendorPayableBill


@pytest.fixture
def test_setup():
    supplier = SupplierProfile(
        business_name="Apex Precision Tools",
        gstin="29ABCDE1234F1Z5",
        udyam_reg_number="UDYAM-KR-03-0012345",
        category=EnterpriseCategory.MICRO,
        activity=EnterpriseActivity.MANUFACTURING,
        upi_vpa="apexprecision@icici",
        phone="+919876543210"
    )
    buyer = BuyerProfile(
        buyer_name="Zenith Machinery Corp",
        gstin="29XYZAB5678K1Z2",
        pan="XYZAB5678K",
        mobile="+919844455566"
    )
    invoice = Invoice(
        id="inv_test_01",
        invoice_number="INV/2026/889",
        invoice_date=date(2026, 11, 1),
        amount=150000.0,
        buyer=buyer,
        has_written_agreement=True,
        agreed_credit_days=30
    )
    return supplier, buyer, invoice


def test_upi_link_generation():
    link = UPILinkGenerator.generate_upi_link(
        vpa="apex@upi",
        payee_name="Apex Tools",
        amount=50000.0,
        invoice_number="INV-100"
    )
    assert link.startswith("upi://pay?")
    assert "pa=apex%40upi" in link or "pa=apex@upi" in link
    assert "am=50000.00" in link


def test_whatsapp_message_in_all_languages(test_setup):
    supplier, buyer, invoice = test_setup
    engine = StatutoryEngine()
    evaluation = engine.evaluate_invoice(invoice, supplier, as_of_date=date(2026, 11, 20))

    # Test English
    msg_en = WhatsAppCadenceManager.generate_message(
        stage=CadenceStage.STAGE_MINUS_5,
        invoice=invoice,
        supplier=supplier,
        evaluation=evaluation,
        language=SupportedLanguage.ENGLISH
    )
    assert "Section 43B(h)" in msg_en
    assert "INV/2026/889" in msg_en
    assert "upi://pay" in msg_en

    # Test Hindi
    msg_hi = WhatsAppCadenceManager.generate_message(
        stage=CadenceStage.STAGE_MINUS_5,
        invoice=invoice,
        supplier=supplier,
        evaluation=evaluation,
        language=SupportedLanguage.HINDI
    )
    assert "धारा 43B(h)" in msg_hi
    assert "INV/2026/889" in msg_hi

    # Test Kannada
    msg_kn = WhatsAppCadenceManager.generate_message(
        stage=CadenceStage.STAGE_MINUS_5,
        invoice=invoice,
        supplier=supplier,
        evaluation=evaluation,
        language=SupportedLanguage.KANNADA
    )
    assert "ಸೆಕ್ಷನ್ 43B(h)" in msg_kn
    assert "INV/2026/889" in msg_kn


def test_auto_reconciliation(test_setup):
    _, _, invoice = test_setup
    recon = ReconciliationEngine()

    credit = BankCreditEntry(
        id="txn_01",
        transaction_date=date(2026, 11, 15),
        amount=150000.0,
        utr_number="CITIN261115001",
        narration="NEFT/CITIN261115001/INV/2026/889/ZENITH"
    )

    matched_inv, score, reason = recon.match_credit_to_invoices(credit, [invoice])
    assert matched_inv is not None
    assert matched_inv.id == invoice.id
    assert score >= 0.95
    assert "INV/2026/889" in reason

    recon.apply_reconciliation(credit, matched_inv)
    assert matched_inv.outstanding_amount == 0.0
    assert matched_inv.status.value == "PAID"


def test_year_end_exposure_scanner():
    scanner = Section43bScanner()

    bills = [
        VendorPayableBill(
            bill_id="b1",
            bill_number="BILL-001",
            vendor_name="Shree Ganesh Castings (Micro)",
            udyam_reg_number="UDYAM-MH-01-0001",
            enterprise_category=EnterpriseCategory.MICRO,
            enterprise_activity=EnterpriseActivity.MANUFACTURING,
            bill_date=date(2026, 10, 1),
            bill_amount=200000.0,
            has_written_agreement=False,
            agreed_credit_days=15
        ),
        VendorPayableBill(
            bill_id="b2",
            bill_number="BILL-002",
            vendor_name="National Steel Traders (Trader Excluded)",
            udyam_reg_number="UDYAM-DL-01-0002",
            enterprise_category=EnterpriseCategory.SMALL,
            enterprise_activity=EnterpriseActivity.TRADING,
            bill_date=date(2026, 10, 1),
            bill_amount=500000.0,
            has_written_agreement=True,
            agreed_credit_days=30
        )
    ]

    # Run audit as of Dec 1, 2026 (bill 1 is overdue)
    audit = scanner.scan_purchase_ledger(bills, as_of_date=date(2026, 12, 1))

    assert audit.total_bills_audited == 2
    assert audit.micro_small_vendors_count == 1
    # Only bill 1 is micro manufacturer covered
    assert audit.section_43bh_disallowed_amount == 200000.0
    assert audit.estimated_additional_tax_liability > 0.0
    assert audit.form_3cd_clause_22["tax_disallowed_under_43bh"] == 200000.0
