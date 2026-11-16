"""
DueClock CA Partner Portal & Distribution Engine
Manages CA partner onboarding, multi-client audit oversight, Form 3CD Clause 22 schedule generation,
and tracks the 20% recurring referral commission ledger.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from dueclock.models import SupplierProfile, EnterpriseCategory, EnterpriseActivity


class ClientPracticeEntry(BaseModel):
    client_id: str
    client_name: str
    gstin: str
    udyam_reg_number: str
    enterprise_category: EnterpriseCategory = EnterpriseCategory.MICRO
    enterprise_activity: EnterpriseActivity = EnterpriseActivity.MANUFACTURING
    active_subscription_tier: str = "STARTER"  # STARTER (6,500), GROWTH (24,000), PRO (60,000)
    annual_subscription_fee: float = 6500.0
    referral_commission_rate: float = 0.20     # 20% recurring commission
    total_receivables_audited: float = 0.0
    total_43bh_disallowed: float = 0.0
    total_accrued_statutory_interest: float = 0.0
    clause_22_ready: bool = True
    onboarded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CAPartnerProfile(BaseModel):
    id: str
    firm_name: str
    partner_name: str
    icai_firm_reg_number: str
    membership_number: str
    email: str
    mobile: str
    city: str = "Bengaluru"
    payout_upi_vpa: str
    referral_code: str
    clients: List[ClientPracticeEntry] = []
    total_commission_earned: float = 0.0
    total_commission_paid: float = 0.0

    @property
    def pending_commission_balance(self) -> float:
        return max(0.0, round(self.total_commission_earned - self.total_commission_paid, 2))


class CAPartnerManager:
    """
    Manages ICAI CA firm partnerships, multi-client tax audit data consolidation,
    and automated referral payouts.
    """

    def __init__(self):
        self.partners: Dict[str, CAPartnerProfile] = {}
        self._seed_demo_ca()

    def _seed_demo_ca(self):
        """Initializes a representative Bengaluru CA practice with 3 active manufacturing clients."""
        ca = CAPartnerProfile(
            id="ca_blr_001",
            firm_name="Ranganathan & Seshadri Chartered Accountants",
            partner_name="CA K. Ranganathan, FCA",
            icai_firm_reg_number="008452S",
            membership_number="214589",
            email="kranganathan@rs-ca.in",
            mobile="+919845098765",
            city="Bengaluru",
            payout_upi_vpa="kranganathan@icici",
            referral_code="RSCA-BLR20"
        )

        # 3 clients
        c1 = ClientPracticeEntry(
            client_id="c_01",
            client_name="Precision Metal Components Pvt Ltd",
            gstin="29ABCDE1234F1Z5",
            udyam_reg_number="UDYAM-KR-03-0012345",
            enterprise_category=EnterpriseCategory.MICRO,
            enterprise_activity=EnterpriseActivity.MANUFACTURING,
            active_subscription_tier="STARTER",
            annual_subscription_fee=6500.0,
            total_receivables_audited=1845000.0,
            total_43bh_disallowed=525000.0,
            total_accrued_statutory_interest=24580.0
        )
        c2 = ClientPracticeEntry(
            client_id="c_02",
            client_name="Kaveri Toolings & Dies LLP",
            gstin="29XYZAB7890K1Z3",
            udyam_reg_number="UDYAM-KR-03-0088991",
            enterprise_category=EnterpriseCategory.SMALL,
            enterprise_activity=EnterpriseActivity.MANUFACTURING,
            active_subscription_tier="GROWTH",
            annual_subscription_fee=24000.0,
            total_receivables_audited=4200000.0,
            total_43bh_disallowed=780000.0,
            total_accrued_statutory_interest=36400.0
        )
        c3 = ClientPracticeEntry(
            client_id="c_03",
            client_name="Apex Aerospace Fasteners",
            gstin="29LMNOP4321P1Z9",
            udyam_reg_number="UDYAM-KR-03-0044552",
            enterprise_category=EnterpriseCategory.MICRO,
            enterprise_activity=EnterpriseActivity.MANUFACTURING,
            active_subscription_tier="STARTER",
            annual_subscription_fee=6500.0,
            total_receivables_audited=980000.0,
            total_43bh_disallowed=145000.0,
            total_accrued_statutory_interest=6200.0
        )

        ca.clients = [c1, c2, c3]
        # Calculate commission: 20% of (6,500 + 24,000 + 6,500) = 20% of 37,000 = 7,400
        ca.total_commission_earned = sum(c.annual_subscription_fee * c.referral_commission_rate for c in ca.clients)
        ca.total_commission_paid = 5000.0

        self.partners[ca.id] = ca

    def get_ca_dashboard(self, ca_id: str) -> Dict[str, Any]:
        """Consolidates cross-client metrics for CA review."""
        if ca_id not in self.partners:
            return {}

        ca = self.partners[ca_id]
        total_audited = sum(c.total_receivables_audited for c in ca.clients)
        total_disallowed = sum(c.total_43bh_disallowed for c in ca.clients)
        total_interest = sum(c.total_accrued_statutory_interest for c in ca.clients)

        return {
            "partner_profile": ca.model_dump(),
            "total_clients_count": len(ca.clients),
            "consolidated_receivables_audited": total_audited,
            "consolidated_43bh_tax_disallowed": total_disallowed,
            "consolidated_msmed_interest_penalty": total_interest,
            "estimated_client_tax_savings_achieved": round(total_disallowed * 0.25168, 2),
            "total_commission_earned": ca.total_commission_earned,
            "pending_commission_payout": ca.pending_commission_balance,
            "clients": [c.model_dump() for c in ca.clients]
        }

    def register_client_referral(
        self,
        referral_code: str,
        client_name: str,
        gstin: str,
        udyam_number: str,
        tier: str = "STARTER"
    ) -> Optional[ClientPracticeEntry]:
        """Registers a new client under the referring CA partner and awards 20% commission."""
        matched_ca = None
        for ca in self.partners.values():
            if ca.referral_code.upper() == referral_code.upper():
                matched_ca = ca
                break

        if not matched_ca:
            return None

        tier_fee = 24000.0 if tier == "GROWTH" else (60000.0 if tier == "PRO" else 6500.0)
        entry = ClientPracticeEntry(
            client_id=f"client_{len(matched_ca.clients) + 1}",
            client_name=client_name,
            gstin=gstin,
            udyam_reg_number=udyam_number,
            active_subscription_tier=tier,
            annual_subscription_fee=tier_fee,
            referral_commission_rate=0.20
        )
        matched_ca.clients.append(entry)
        matched_ca.total_commission_earned += (tier_fee * 0.20)
        return entry
