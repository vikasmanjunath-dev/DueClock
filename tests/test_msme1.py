"""
Unit tests for Form MSME-1 Filing Schedule Generator
"""

import pytest
from datetime import date
from dueclock.msme1_generator import MSME1Generator, MSME1Entry


def test_msme1_schedule_generation():
    generator = MSME1Generator()

    entries = [
        MSME1Entry(
            supplier_name="Precision Metal Components",
            supplier_pan="ABCDE1234F",
            supplier_udyam_reg="UDYAM-KR-03-0012345",
            invoice_number="INV/2026/041",
            invoice_date=date(2026, 10, 1),
            statutory_due_date=date(2026, 10, 16),
            amount_due=145000.0,
            delay_in_days=22,
            reason_for_delay="Pending vendor invoice reconciliation"
        ),
        MSME1Entry(
            supplier_name="Zenith Tooling Systems",
            supplier_pan="XYZAB2222K",
            supplier_udyam_reg="UDYAM-KR-03-0088991",
            invoice_number="INV/2026/028",
            invoice_date=date(2026, 9, 15),
            statutory_due_date=date(2026, 9, 30),
            amount_due=380000.0,
            delay_in_days=38,
            reason_for_delay="Awaiting quality approval signoff"
        )
    ]

    schedule = generator.generate_schedule(
        company_name="Mega Auto Ancillary Ltd",
        company_cin="U29100KA2015PLC081234",
        company_gstin="29AAACM1234H1Z1",
        delayed_entries=entries,
        as_of_date=date(2026, 11, 7)
    )

    assert schedule.total_delayed_msme_amount == 525000.0
    assert schedule.total_delayed_vendors_count == 2
    assert schedule.half_year_period == "October to March"
    assert "FY 2026-27" in schedule.financial_year

    csv_data = generator.export_csv(schedule)
    assert "MCA FORM MSME-1 FILING SCHEDULE" in csv_data
    assert "525000.00" in csv_data
    assert "UDYAM-KR-03-0012345" in csv_data
