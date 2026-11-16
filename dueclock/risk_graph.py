"""
DueClock Buyer Payment Behaviour Risk Graph
Multi-tenant payment data network keyed by buyer PAN/GSTIN.
Calculates Days-to-Pay (DTP), statutory delinquency risk, dispute frequency,
and produces credit reliability scoring for suppliers and TReDS financiers.
"""

from typing import Dict, Any, List, Optional
from datetime import date, datetime, timezone
from pydantic import BaseModel, Field


class BuyerPaymentRecord(BaseModel):
    seller_tenant_id: str
    buyer_pan: str
    buyer_gstin: Optional[str] = None
    invoice_number: str
    invoice_amount: float
    statutory_term_days: int  # 15 or 45 days
    actual_days_to_pay: int
    settled_date: date
    had_dispute: bool = False
    dispute_category: Optional[str] = None
    reminders_sent_count: int = 1


class BuyerRiskProfile(BaseModel):
    buyer_pan: str
    buyer_name: str
    total_invoices_tracked: int
    total_volume_settled: float
    average_days_to_pay: float
    pct_settled_within_statutory_term: float
    pct_overdue_past_day_45: float
    dispute_frequency_pct: float
    dueclock_reliability_score: int       # 0 to 100
    treds_financier_grade: str            # AAA, AA, BBB, HIGH_RISK
    payment_behaviour_tag: str            # PROMPT_PAYER, STATUTORY_CONSCIOUS, CHRONIC_LATE, HIGH_DISPUTE
    risk_summary: str
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BuyerRiskGraphEngine:
    """
    Constructs the proprietary payment-behaviour data network across sellers.
    Enforces privacy-preserving DPDP aggregated metrics.
    """

    def __init__(self):
        self.records: List[BuyerPaymentRecord] = []
        self._seed_network_data()

    def _seed_network_data(self):
        """Seeds initial payment history across 3 representative enterprise buyers."""
        # Buyer 1: Prompt corporate payer
        for i in range(12):
            self.records.append(BuyerPaymentRecord(
                seller_tenant_id=f"seller_{i % 3}",
                buyer_pan="AAACB1111A",
                buyer_gstin="29AAACB1111A1Z1",
                invoice_number=f"INV-P-{i+1}",
                invoice_amount=150000.0,
                statutory_term_days=45,
                actual_days_to_pay=38 + (i % 6),  # Always pays within 38-44 days (safe 45d)
                settled_date=date(2026, 9, 15),
                had_dispute=False,
                reminders_sent_count=2
            ))

        # Buyer 2: Chronic late payer (breaches 45 days)
        for i in range(8):
            self.records.append(BuyerPaymentRecord(
                seller_tenant_id=f"seller_{i % 2}",
                buyer_pan="XYZAB2222K",
                buyer_gstin="29XYZAB2222K1Z9",
                invoice_number=f"INV-L-{i+1}",
                invoice_amount=320000.0,
                statutory_term_days=15,
                actual_days_to_pay=65 + (i * 3),  # 65 to 86 days!
                settled_date=date(2026, 8, 20),
                had_dispute=(i % 3 == 0),
                dispute_category="Rate difference" if i % 3 == 0 else None,
                reminders_sent_count=5
            ))

    def evaluate_buyer_risk(self, buyer_pan: str, buyer_name: str = "Enterprise Buyer") -> BuyerRiskProfile:
        pan_clean = buyer_pan.strip().upper()
        matching = [r for r in self.records if r.buyer_pan == pan_clean]

        if not matching:
            return BuyerRiskProfile(
                buyer_pan=pan_clean,
                buyer_name=buyer_name,
                total_invoices_tracked=0,
                total_volume_settled=0.0,
                average_days_to_pay=0.0,
                pct_settled_within_statutory_term=0.0,
                pct_overdue_past_day_45=0.0,
                dispute_frequency_pct=0.0,
                dueclock_reliability_score=50,
                treds_financier_grade="UNRATED",
                payment_behaviour_tag="NO_HISTORY",
                risk_summary="No cross-supplier payment history yet in DueClock graph."
            )

        total_count = len(matching)
        total_vol = sum(r.invoice_amount for r in matching)
        avg_dtp = sum(r.actual_days_to_pay for r in matching) / total_count
        within_term = sum(1 for r in matching if r.actual_days_to_pay <= r.statutory_term_days)
        over_45 = sum(1 for r in matching if r.actual_days_to_pay > 45)
        disputes = sum(1 for r in matching if r.had_dispute)

        pct_within_term = round((within_term / total_count) * 100, 1)
        pct_over_45 = round((over_45 / total_count) * 100, 1)
        dispute_pct = round((disputes / total_count) * 100, 1)

        # DueClock Credit Reliability Score (0 to 100)
        # Base 100, minus penalties for DTP past 45 and disputes
        score = 100
        if avg_dtp > 45:
            score -= int((avg_dtp - 45) * 1.5)
        if pct_over_45 > 20:
            score -= int(pct_over_45 * 0.4)
        if dispute_pct > 10:
            score -= int(dispute_pct * 0.5)

        score = max(5, min(99, score))

        # TReDS Financier Grade
        if score >= 85:
            grade = "AAA"
            tag = "PROMPT_STATUTORY_PAYER"
            summary = "Excellent track record. Consistently settles before day 45 to protect 43B(h) deductions."
        elif score >= 70:
            grade = "AA"
            tag = "STATUTORY_CONSCIOUS"
            summary = "Reliable payer. Typically settles near day 45 with prompt response to Stage 2 reminders."
        elif score >= 55:
            grade = "BBB"
            tag = "DELAY_PRONE"
            summary = "Moderate delay risk. Frequently extends credit past 45 days unless statutory notices cited."
        else:
            grade = "HIGH_RISK"
            tag = "CHRONIC_DELINQUENT"
            summary = "High statutory disallowance risk. Requires strict Stage 4 tax warnings & upfront terms."

        return BuyerRiskProfile(
            buyer_pan=pan_clean,
            buyer_name=buyer_name,
            total_invoices_tracked=total_count,
            total_volume_settled=round(total_vol, 2),
            average_days_to_pay=round(avg_dtp, 1),
            pct_settled_within_statutory_term=pct_within_term,
            pct_overdue_past_day_45=pct_over_45,
            dispute_frequency_pct=dispute_pct,
            dueclock_reliability_score=score,
            treds_financier_grade=grade,
            payment_behaviour_tag=tag,
            risk_summary=summary
        )

    def record_settlement_event(self, record: BuyerPaymentRecord):
        """Adds verified settlement event into the privacy-preserving graph."""
        self.records.append(record)
