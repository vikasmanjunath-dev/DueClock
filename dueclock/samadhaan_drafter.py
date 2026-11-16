"""
DueClock MSME Samadhaan Legal Petition & Reference Drafter
Generates formal statutory claim documents under Section 18 of the MSMED Act, 2006
for submission to the Micro and Small Enterprises Facilitation Council (MSEFC).
"""

from typing import Dict, Any, List, Optional
from datetime import date
from dueclock.models import SupplierProfile, Invoice, StatutoryEvaluation
from dueclock.engine import StatutoryEngine


class SamadhaanPetitionDrafter:
    """
    Drafts formal statutory petitions and claim petitions for the Ministry of MSME Samadhaan Portal.
    Includes Section 18 reference prayer and Section 19 75% court pre-deposit warning.
    """

    def __init__(self):
        self.engine = StatutoryEngine()

    def generate_samadhaan_petition(
        self,
        supplier: SupplierProfile,
        invoice: Invoice,
        evaluation: StatutoryEvaluation,
        as_of_date: Optional[date] = None
    ) -> str:
        if as_of_date is None:
            as_of_date = date.today()

        total_claim = round(invoice.outstanding_amount + evaluation.accrued_statutory_interest, 2)
        statutory_days = evaluation.effective_days_granted
        overdue_days = max(0, (as_of_date - evaluation.statutory_due_date).days)

        petition_text = f"""
========================================================================================
           FORM OF REFERENCE APPLICATION UNDER SECTION 18(1) OF THE
  MICRO, SMALL AND MEDIUM ENTERPRISES DEVELOPMENT ACT, 2006 (MSMED ACT, 2006)
          BEFORE THE MICRO AND SMALL ENTERPRISES FACILITATION COUNCIL (MSEFC)
========================================================================================

IN THE MATTER OF DELAYED PAYMENTS UNDER SECTION 15, 16 & 18 OF THE MSMED ACT, 2006:

CLAIMANT / APPLICANT:
Name:              {supplier.business_name} (Trade Name: {supplier.trade_name or supplier.business_name})
Udyam Reg. Number: {supplier.udyam_reg_number}
Category:          {supplier.category.value} ENTERPRISE ({supplier.activity.value})
GSTIN:             {supplier.gstin}
Address:           {supplier.address or "Industrial Estate, Bengaluru, Karnataka"}
Authorized VPA:    {supplier.upi_vpa}
Contact:           {supplier.phone} | {supplier.email or "accounts@supplier.in"}

VERSUS

RESPONDENT / BUYER:
Buyer Name:        {invoice.buyer.buyer_name}
GSTIN:             {invoice.buyer.gstin or "Declared in Purchase Order"}
PAN:               {invoice.buyer.pan or "Declared in Voucher Records"}
Mobile / Contact:  {invoice.buyer.mobile}

----------------------------------------------------------------------------------------
                             STATEMENT OF CLAIM & MATERIAL FACTS
----------------------------------------------------------------------------------------

1. The Claimant is a duly registered Micro/Small enterprise possessing valid Udyam 
   Registration No. {supplier.udyam_reg_number}, engaged in {supplier.activity.value.lower()}.

2. The Claimant supplied goods/services to the Respondent under Invoice #{invoice.invoice_number}
   dated {invoice.invoice_date.strftime('%d-%b-%Y')} for a principal value of ₹{invoice.amount:,.2f}.

3. Goods were duly delivered and accepted without quality dispute or notice within 
   the statutory 15-day deemed acceptance window.

4. STATUTORY MANDATE UNDER SECTION 15:
   Under Section 15 of the MSMED Act, the Respondent was legally mandated to clear 
   payment within {statutory_days} days, making the statutory due date {evaluation.statutory_due_date.strftime('%d-%b-%Y')}.
   The Respondent has breached this statutory timeline by {overdue_days} days.

5. STATUTORY COMPOUND INTEREST UNDER SECTION 16:
   Section 16 mandates that any delayed payment attracts compound interest with monthly rests 
   at three times (3x) the Reserve Bank of India Bank Rate ({evaluation.annual_interest_rate_pct}% p.a.).
   - Principal Outstanding:                 ₹{invoice.outstanding_amount:,.2f}
   - Accrued Compound Interest (Sec 16):    ₹{evaluation.accrued_statutory_interest:,.2f}
   - TOTAL STATUTORY CLAIM PAYABLE:        ₹{total_claim:,.2f}

6. SECTION 23 INCOME TAX DISALLOWANCE:
   Under Section 23 of the MSMED Act read with Section 43B(h) of the Income Tax Act, 1961, 
   interest payable under Section 16 is strictly NOT deductible from the Respondent's income.

----------------------------------------------------------------------------------------
                                       PRAYER
----------------------------------------------------------------------------------------

The Claimant respectfully prays that this Hon'ble Facilitation Council be pleased to:

(a) Direct the Respondent to forthwith pay the principal outstanding amount of ₹{invoice.outstanding_amount:,.2f};
(b) Direct the Respondent to pay compound statutory interest of ₹{evaluation.accrued_statutory_interest:,.2f} accrued up to {as_of_date.strftime('%d-%b-%Y')};
(c) Direct the Respondent to pay further compound interest with monthly rests at 3x the RBI Bank Rate from {as_of_date.strftime('%d-%b-%Y')} until full realization;
(d) Notify the Respondent of Section 19 of the MSMED Act, which mandates a 75% pre-deposit before any court can entertain an appeal or challenge against the Council's decree.

Place: Bengaluru
Date:  {as_of_date.strftime('%d-%b-%Y')}

                                             For {supplier.business_name}
                                             Authorized Signatory / Managing Partner
========================================================================================
"""
        return petition_text.strip()
