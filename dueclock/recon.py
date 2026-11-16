"""
DueClock Auto-Reconciliation Engine
Matches bank credit transactions and UPI UTR narrations to open invoices.
Automatically silences WhatsApp reminder cadences upon confirmed settlement.
"""

import re
from typing import List, Tuple, Optional
from dueclock.models import Invoice, BankCreditEntry, InvoiceStatus


class ReconciliationEngine:
    """
    Automated matching engine for bank narration text and payment credits.
    Protects customer relationships by instantly stopping reminder cadences when paid.
    """

    @staticmethod
    def clean_text(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", s).upper()

    def match_credit_to_invoices(
        self,
        credit: BankCreditEntry,
        open_invoices: List[Invoice]
    ) -> Tuple[Optional[Invoice], float, str]:
        """
        Attempts to match an incoming bank credit to an open invoice.
        Returns: (matched_invoice, confidence_score, match_reason)
        """
        narration_clean = self.clean_text(credit.narration)

        # 1. Exact Invoice Number in Narration
        for inv in open_invoices:
            inv_clean = self.clean_text(inv.invoice_number)
            if inv_clean and inv_clean in narration_clean:
                # Check amount
                if abs(credit.amount - inv.outstanding_amount) < 1.0:
                    return (inv, 1.0, f"Exact Invoice #{inv.invoice_number} & Exact Amount Match")
                elif credit.amount < inv.outstanding_amount:
                    return (inv, 0.90, f"Exact Invoice #{inv.invoice_number} (Partial Payment)")

        # 2. Exact UTR check if available
        if credit.utr_number:
            utr_clean = self.clean_text(credit.utr_number)
            for inv in open_invoices:
                if utr_clean in narration_clean and abs(credit.amount - inv.outstanding_amount) < 1.0:
                    return (inv, 0.95, f"UTR Reference {credit.utr_number} matched")

        # 3. Exact Amount + Buyer Name Substring Match
        candidates = []
        for inv in open_invoices:
            if abs(credit.amount - inv.outstanding_amount) < 1.0:
                buyer_tokens = [self.clean_text(tok) for tok in inv.buyer.buyer_name.split() if len(tok) > 3]
                matched_tokens = [tok for tok in buyer_tokens if tok in narration_clean]
                if matched_tokens:
                    candidates.append((inv, 0.85, f"Exact Amount + Buyer Name Match ({', '.join(matched_tokens)})"))

        if len(candidates) == 1:
            return candidates[0]

        # 4. Solitary Exact Amount Match across all open invoices
        amount_matches = [inv for inv in open_invoices if abs(credit.amount - inv.outstanding_amount) < 1.0]
        if len(amount_matches) == 1:
            return (amount_matches[0], 0.75, "Unique Exact Amount Match")

        return (None, 0.0, "No confident match found")

    def apply_reconciliation(
        self,
        credit: BankCreditEntry,
        invoice: Invoice
    ) -> Invoice:
        """Applies credit against invoice and updates payment status."""
        new_paid = round(invoice.paid_amount + credit.amount, 2)
        invoice.paid_amount = new_paid

        if invoice.paid_amount >= (invoice.amount - 0.5):
            invoice.status = InvoiceStatus.PAID
        else:
            invoice.status = InvoiceStatus.PARTIALLY_PAID

        credit.matched_invoice_id = invoice.id
        return invoice
