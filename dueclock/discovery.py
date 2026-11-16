"""
DueClock Customer Discovery Framework & Interview Kit
Operationalizes the 20 structured discovery questions for Bengaluru micro-manufacturers.
Evaluates pilot readiness, pain severity, and willingness to pay.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class DiscoveryQuestion(BaseModel):
    id: int
    category: str
    question_text: str
    objective: str


DISCOVERY_QUESTIONS: List[DiscoveryQuestion] = [
    # Their payments today
    DiscoveryQuestion(id=1, category="Their Payments Today", question_text="Walk me through the last invoice that was paid late. What happened?", objective="Uncover exact delay friction, excuses, and communication breakdown."),
    DiscoveryQuestion(id=2, category="Their Payments Today", question_text="How many of your invoices are unpaid past 45 days right now?", objective="Measure immediate recoverable volume and Section 43B(h) disallowance size."),
    DiscoveryQuestion(id=3, category="Their Payments Today", question_text="Who chases, how, and how many hours a week does it take?", objective="Quantify owner/accountant labour cost wasted on manual follow-ups."),
    DiscoveryQuestion(id=4, category="Their Payments Today", question_text="What did the last late payment cost you: interest on CC loan, a missed order, or delayed salary?", objective="Pinpoint tangible economic damage caused by working capital block."),
    DiscoveryQuestion(id=5, category="Their Payments Today", question_text="Have you ever asked a buyer to pay interest for late payment? What happened?", objective="Validate why MSMED 3x bank rate interest is rarely invoked without automated software backing."),

    # The law and the buyers
    DiscoveryQuestion(id=6, category="The Law and the Buyers", question_text="Do you have an Udyam certificate? Under which activity type (Manufacturing/Services vs Trading)?", objective="Verify Section 43B(h) eligibility (exclude traders)."),
    DiscoveryQuestion(id=7, category="The Law and the Buyers", question_text="Do your buyers know about the 45-day rule? How do you know?", objective="Assess buyer awareness of Income Tax Section 43B(h)."),
    DiscoveryQuestion(id=8, category="The Law and the Buyers", question_text="Do you have written payment terms with each buyer? Which ones?", objective="Determine 15-day default cap vs 45-day contract cap split."),
    DiscoveryQuestion(id=9, category="The Law and the Buyers", question_text="Has any buyer mentioned their tax deduction when paying?", objective="Check if buyer CAs are forcing compliance during audit season."),
    DiscoveryQuestion(id=10, category="The Law and the Buyers", question_text="Which buyers would be hurt most by a late-payment notice, and which would you never annoy?", objective="Determine customer relationship sensitivities and whitelisting requirements."),

    # Tools and people
    DiscoveryQuestion(id=11, category="Tools and People", question_text="How do you keep accounts today: TallyPrime, another package, Excel?", objective="Confirm TallyPrime compatibility and release version."),
    DiscoveryQuestion(id=12, category="Tools and People", question_text="Who enters data, and who sees the outstanding list?", objective="Identify exact persona (Tally operator vs owner vs CA)."),
    DiscoveryQuestion(id=13, category="Tools and People", question_text="Does your CA review receivables, or only file returns?", objective="Assess CA influence on software adoption and referral loops."),
    DiscoveryQuestion(id=14, category="Tools and People", question_text="What have you tried for reminders before, and why did it stop?", objective="Identify previous failed tools (manual WhatsApp, emails, spreadsheets)."),
    DiscoveryQuestion(id=15, category="Tools and People", question_text="Which WhatsApp or SMS messages do your buyers actually answer?", objective="Tune utility reminder tone and messaging channels."),

    # Willingness to pay
    DiscoveryQuestion(id=16, category="Willingness to Pay", question_text="If reminders recovered one late invoice a month, how much would that be worth to you?", objective="Anchor ROI against annual ₹6,500 subscription price."),
    DiscoveryQuestion(id=17, category="Willingness to Pay", question_text="What do you pay now for Tally TSS, your CA and any software, per year?", objective="Benchmark tech budget ceiling for small factory."),
    DiscoveryQuestion(id=18, category="Willingness to Pay", question_text="Who would have to approve a ₹6,500 yearly spend?", objective="Locate single economic buyer (Director/Proprietor)."),
    DiscoveryQuestion(id=19, category="Willingness to Pay", question_text="Would you let us install a read-only connector on the Tally computer? Who decides that?", objective="Test connector installation friction and security objections."),
    DiscoveryQuestion(id=20, category="Willingness to Pay", question_text="If I came back in two weeks with a trial on your data, what would you need to see to sign?", objective="Obtain clear criteria for closing founding pilot at ₹3,250.")
]


class DiscoveryInterviewRecord(BaseModel):
    id: str
    company_name: str
    contact_person: str
    phone: str
    industrial_cluster: str = "Peenya, Bengaluru"
    enterprise_type: str = "Micro Manufacturer"
    annual_turnover_cr: float = 4.5
    tally_version: str = "TallyPrime 4.1"
    answers: Dict[int, str] = {}
    pilot_readiness_score: int = 0  # 1 to 10
    committed_to_pilot: bool = False
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DiscoveryEngine:
    """Manages founder customer discovery sessions and scores pilot conversion readiness."""

    def __init__(self):
        self.interviews: List[DiscoveryInterviewRecord] = []

    def score_interview(self, answers: Dict[int, str], has_tally: bool, has_udyam: bool) -> int:
        score = 0
        if has_tally:
            score += 3
        if has_udyam:
            score += 2
        # Willingness to pay checks
        if any("yes" in answers.get(q, "").lower() for q in [16, 18, 19, 20]):
            score += 3
        if "severe" in answers.get(4, "").lower() or "interest" in answers.get(4, "").lower():
            score += 2
        return min(10, score)
