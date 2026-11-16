"""
Unit tests for CA Partner Portal & Distribution Engine
"""

import pytest
from dueclock.ca_partner import CAPartnerManager


def test_ca_dashboard_aggregation():
    manager = CAPartnerManager()
    dash = manager.get_ca_dashboard("ca_blr_001")

    assert dash["total_clients_count"] == 3
    assert dash["consolidated_receivables_audited"] > 0
    assert dash["consolidated_43bh_tax_disallowed"] > 0
    assert dash["total_commission_earned"] == 7400.0  # 20% of (6500 + 24000 + 6500)
    assert dash["pending_commission_payout"] == 2400.0  # 7400 - 5000 paid


def test_register_client_referral():
    manager = CAPartnerManager()
    entry = manager.register_client_referral(
        referral_code="RSCA-BLR20",
        client_name="Bangalore Die Works",
        gstin="29AAABB1111C1Z5",
        udyam_number="UDYAM-KR-03-0055112",
        tier="STARTER"
    )

    assert entry is not None
    assert entry.client_name == "Bangalore Die Works"
    assert entry.annual_subscription_fee == 6500.0

    dash = manager.get_ca_dashboard("ca_blr_001")
    assert dash["total_clients_count"] == 4
    # Commission should have increased by 20% of 6,500 = 1,300
    assert dash["total_commission_earned"] == 7400.0 + 1300.0
