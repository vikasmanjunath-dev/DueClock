"""
DueClock WhatsApp Reminders Cadence & UPI Generator
Generates Meta-compliant utility reminder templates in English, Hindi, and Kannada.
Includes zero-custody UPI deep link generation.
"""

from typing import Dict, Any, Optional
import urllib.parse
from datetime import date
from dueclock.models import (
    Invoice,
    SupplierProfile,
    StatutoryEvaluation,
    CadenceStage,
    SupportedLanguage
)


class UPILinkGenerator:
    """Generates NPCI-compliant UPI deep links direct to supplier VPA."""

    @staticmethod
    def generate_upi_link(
        vpa: str,
        payee_name: str,
        amount: float,
        invoice_number: str
    ) -> str:
        params = {
            "pa": vpa.strip(),
            "pn": payee_name.strip(),
            "am": f"{amount:.2f}",
            "cu": "INR",
            "tn": f"Bill {invoice_number} DueClock"
        }
        encoded = urllib.parse.urlencode(params)
        return f"upi://pay?{encoded}"


class WhatsAppCadenceManager:
    """
    Utility-classified WhatsApp messaging engine.
    Follows Meta Business API standards with multi-lingual templating.
    """

    TEMPLATES: Dict[SupportedLanguage, Dict[CadenceStage, str]] = {
        SupportedLanguage.ENGLISH: {
            CadenceStage.STAGE_MINUS_5: (
                "Hello {buyer_name},\n\n"
                "This is a courtesy reminder from {supplier_name}. Invoice *#{invoice_number}* for *₹{amount:,.2f}* "
                "is due on *{due_date}* per statutory MSMED Section 15 terms.\n\n"
                "To preserve Section 43B(h) tax deduction compliance and avoid statutory interest, "
                "settle seamlessly via your supplier's direct UPI link:\n"
                "{upi_link}\n\n"
                "Review invoice & deduction impact: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_0: (
                "Important: Invoice Due Today\n\n"
                "Hello {buyer_name}, Invoice *#{invoice_number}* for *₹{amount:,.2f}* from {supplier_name} "
                "is due for payment today (*{due_date}*).\n\n"
                "Under Section 43B(h) of the Income Tax Act, delayed payments beyond the statutory term lose income tax deductibility.\n\n"
                "Pay instantly via UPI (Zero Fee):\n{upi_link}\n\n"
                "Need to schedule a date? Visit: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_PLUS_5: (
                "Statutory Overdue Notice: Interest Accrual Active\n\n"
                "Dear {buyer_name}, Invoice *#{invoice_number}* (₹{amount:,.2f}) from {supplier_name} is now *5 days overdue*.\n\n"
                "Per Section 16 of the MSMED Act, compound interest at 3x the RBI bank rate (*{statutory_rate}% p.a.*) has commenced accruing.\n"
                "Current Accrued Interest: *₹{accrued_interest:,.2f}*\n\n"
                "Please settle immediately to cap statutory interest:\n{upi_link}\n\n"
                "Details: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_PLUS_15: (
                "URGENT: Section 43B(h) Tax Disallowance Warning\n\n"
                "Attention: Accounts & Finance Head, {buyer_name}.\n"
                "Invoice *#{invoice_number}* for *₹{amount:,.2f}* from {supplier_name} (Micro/Small Manufacturer) is *15 days overdue*.\n\n"
                "⚠️ TAX IMPACT: Under Section 43B(h), this entire expense of ₹{amount:,.2f} will be DISALLOWED from your tax computation, triggering an additional ~30% tax liability on your business.\n\n"
                "Settle today to safeguard deduction:\n{upi_link}\n\n"
                "Download computation report: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_PLUS_30: (
                "FINAL PRE-LEGAL STATUTORY NOTICE\n\n"
                "To: Directors / Accounts Head, {buyer_name}.\n"
                "Invoice *#{invoice_number}* (Principal: ₹{amount:,.2f} + Accrued Interest: ₹{accrued_interest:,.2f}) remains unpaid for 30+ days.\n\n"
                "Notice is hereby given that failing payment within 48 hours, this claim is scheduled for automated submission to the Ministry of MSME *Samadhaan Delayed Payment Portal*.\n\n"
                "Immediate Settlement Link: {upi_link}\n"
                "Audit Summary: {buyer_portal_url}"
            )
        },

        SupportedLanguage.HINDI: {
            CadenceStage.STAGE_MINUS_5: (
                "नमस्ते {buyer_name},\n\n"
                "{supplier_name} से इनवॉइस *#{invoice_number}* (राशि *₹{amount:,.2f}*) की देय तिथि *{due_date}* है।\n"
                "धारा 43B(h) के तहत टैक्स छूट सुरक्षित रखने और 19.5% ब्याज से बचने के लिए, कृपया समय पर भुगतान करें:\n"
                "{upi_link}\n\n"
                "विवरण देखें: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_0: (
                "महत्वपूर्ण: इनवॉइस की अंतिम तिथि आज है\n\n"
                "नमस्ते {buyer_name}, {supplier_name} का इनवॉइस *#{invoice_number}* (₹{amount:,.2f}) का भुगतान आज देय है।\n"
                "इनकम टैक्स धारा 43B(h) के नियमानुसार समय पर भुगतान कर टैक्स छूट सुनिश्चित करें।\n\n"
                "तुरंत UPI द्वारा भुगतान करें:\n{upi_link}\n\n"
                "तारीख शेड्यूल करें: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_PLUS_5: (
                "वैधानिक सूचना: MSME ब्याज गणना शुरू\n\n"
                "प्रिय {buyer_name}, इनवॉइस *#{invoice_number}* (₹{amount:,.2f}) 5 दिन ओवरड्यू हो चुका है।\n"
                "MSMED एक्ट धारा 16 के तहत RBI बैंक दर के 3 गुना (*{statutory_rate}% प्रति वर्ष*) चक्रवृद्ध ब्याज लागू हो गया है।\n"
                "वर्तमान ब्याज: *₹{accrued_interest:,.2f}*\n\n"
                "तुरंत भुगतान लिंक:\n{upi_link}\n\n"
                "पोर्टल: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_PLUS_15: (
                "अति आवश्यक: धारा 43B(h) टैक्स कटौती अस्वीकृति चेतावनी\n\n"
                "ध्यान दें: {buyer_name},\n"
                "इनवॉइस *#{invoice_number}* (₹{amount:,.2f}) 15 दिन ओवरड्यू है।\n"
                "⚠️ धारा 43B(h) के तहत यह पूरी राशि आपके टैक्स से अस्वीकृत (Disallowed) कर दी जाएगी, जिससे 30% अतिरिक्त टैक्स लगेगा।\n\n"
                "टैक्स बचत हेतु आज ही भुगतान करें:\n{upi_link}\n\n"
                "रिपोर्ट देखें: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_PLUS_30: (
                "अंतिम वैधानिक पूर्व-कानूनी सूचना\n\n"
                "प्रति: {buyer_name},\n"
                "इनवॉइस *#{invoice_number}* (मूलधन: ₹{amount:,.2f} + ब्याज: ₹{accrued_interest:,.2f}) 30+ दिनों से लंबित है।\n"
                "कृपया 48 घंटे में भुगतान करें, अन्यथा मामला MSME समाधान पोर्टल पर दर्ज किया जाएगा।\n\n"
                "भुगतान करें:\n{upi_link}\n"
                "विवरण: {buyer_portal_url}"
            )
        },

        SupportedLanguage.KANNADA: {
            CadenceStage.STAGE_MINUS_5: (
                "ನಮಸ್ಕಾರ {buyer_name},\n\n"
                "{supplier_name} ವತಿಯಿಂದ ಇನ್‌ವಾಯ್ಸ್ *#{invoice_number}* (ಮೊತ್ತ *₹{amount:,.2f}*) ರ ಪಾವತಿ ದಿನಾಂಕ *{due_date}* ಆಗಿದೆ.\n"
                "ಸೆಕ್ಷನ್ 43B(h) ಆದಾಯ ತೆರಿಗೆ ವಿನಾಯಿತಿಯನ್ನು ಕಾಪಾಡಿಕೊಳ್ಳಲು ಹಾಗೂ ಬಡ್ಡಿಯನ್ನು ತಪ್ಪಿಸಲು ಸಮಯಕ್ಕೆ ಸರಿಯಾಗಿ ಪಾವತಿಸಿ:\n"
                "{upi_link}\n\n"
                "ವಿವರಗಳಿಗಾಗಿ: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_0: (
                "ಪ್ರಮುಖ: ಇನ್‌ವಾಯ್ಸ್ ಪಾವತಿ ದಿನಾಂಕ ಇಂದು\n\n"
                "ನಮಸ್ಕಾರ {buyer_name}, {supplier_name} ರವರ ಇನ್‌ವಾಯ್ಸ್ *#{invoice_number}* (₹{amount:,.2f}) ಪಾವತಿಗೆ ಇಂದೇ ಅಂತಿಮ ದಿನ.\n"
                "ಆದಾಯ ತೆರಿಗೆ ಕಾಯ್ದೆ 43B(h) ರ ಅನ್ವಯ ತೆರಿಗೆ ವಿನಾಯಿತಿ ಪಡೆಯಲು ಈಗಲೇ ಪಾವತಿಸಿ.\n\n"
                "ತಕ್ಷಣ UPI ಮೂಲಕ ಪಾವತಿಸಿ:\n{upi_link}\n\n"
                "ಪೋರ್ಟಲ್: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_PLUS_5: (
                "ಕಾನೂನುಬದ್ಧ ನೋಟಿಸ್: ಬಡ್ಡಿ ಲೆಕ್ಕಾಚಾರ ಆರಂಭವಾಗಿದೆ\n\n"
                "ಆತ್ಮೀಯ {buyer_name}, ಇನ್‌ವಾಯ್ಸ್ *#{invoice_number}* (₹{amount:,.2f}) 5 ದಿನಗಳ ಕಾಲ ಮೀರಿದೆ.\n"
                "MSMED ಕಾಯ್ದೆ ಸೆಕ್ಷನ್ 16 ರ ಪ್ರಕಾರ RBI ಬ್ಯಾಂಕ್ ದರದ 3 ಪಟ್ಟು (*{statutory_rate}%*) ಚಕ್ರಬಡ್ಡಿ ಅನ್ವಯವಾಗುತ್ತದೆ.\n"
                "ಸಂಚಿತ ಬಡ್ಡಿ: *₹{accrued_interest:,.2f}*\n\n"
                "ಪಾವತಿ ಲಿಂಕ್:\n{upi_link}\n\n"
                "ವಿವರ: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_PLUS_15: (
                "ತುರ್ತು ಎಚ್ಚರಿಕೆ: ಸೆಕ್ಷನ್ 43B(h) ತೆರಿಗೆ ರದ್ದತಿ ಎಚ್ಚರಿಕೆ\n\n"
                "ಗಮನಿಸಿ: {buyer_name},\n"
                "ಇನ್‌ವಾಯ್ಸ್ *#{invoice_number}* (₹{amount:,.2f}) 15 ದಿನಗಳ ಕಾಲ ತಡವಾಗಿದೆ.\n"
                "⚠️ ಸೆಕ್ಷನ್ 43B(h) ರ ಪ್ರಕಾರ ಈ ಪೂರ್ಣ ವೆಚ್ಚವನ್ನು ನಿಮ್ಮ ವ್ಯವಹಾರದ ತೆರಿಗೆ ಲೆಕ್ಕಾಚಾರದಿಂದ ರದ್ದುಗೊಳಿಸಲಾಗುತ್ತದೆ (~30% ಹೆಚ್ಚುವರಿ ತೆರಿಗೆ).\n\n"
                "ತೆರಿಗೆ ಉಳಿಸಲು ಇಂದೇ ಪಾವತಿಸಿ:\n{upi_link}\n\n"
                "ವರದಿ: {buyer_portal_url}"
            ),
            CadenceStage.STAGE_PLUS_30: (
                "ಅಂತಿಮ ಕಾನೂನುಬದ್ಧ ನೋಟಿಸ್\n\n"
                "ಗಮನಕ್ಕೆ: {buyer_name},\n"
                "ಇನ್‌ವಾಯ್ಸ್ *#{invoice_number}* (ಅಸಲು: ₹{amount:,.2f} + ಬಡ್ಡಿ: ₹{accrued_interest:,.2f}) 30+ ದಿನಗಳಿಂದ ಬಾಕಿ ಉಳಿದಿದೆ.\n"
                "48 ಗಂಟೆಗಳಲ್ಲಿ ಪಾವತಿಸದಿದ್ದಲ್ಲಿ ಈ ಪ್ರಕರಣವನ್ನು ಕೇಂದ್ರ MSME ಸಮಾಧಾನ್ ಪೋರ್ಟಲ್‌ಗೆ ದಾಖಲಿಸಲಾಗುವುದು.\n\n"
                "ಪಾವತಿ ಲಿಂಕ್: {upi_link}\n"
                "ವಿವರ: {buyer_portal_url}"
            )
        }
    }

    @classmethod
    def determine_cadence_stage(cls, evaluation: StatutoryEvaluation) -> Optional[CadenceStage]:
        """Determines the appropriate reminder stage based on days remaining / overdue."""
        if not evaluation.is_overdue:
            if evaluation.days_remaining in (4, 5, 6):
                return CadenceStage.STAGE_MINUS_5
            elif evaluation.days_remaining <= 0:
                return CadenceStage.STAGE_0
            return None
        else:
            overdue_days = abs(evaluation.days_remaining)
            if overdue_days >= 30:
                return CadenceStage.STAGE_PLUS_30
            elif overdue_days >= 15:
                return CadenceStage.STAGE_PLUS_15
            elif overdue_days >= 5:
                return CadenceStage.STAGE_PLUS_5
            else:
                return CadenceStage.STAGE_0

    @classmethod
    def generate_message(
        cls,
        stage: CadenceStage,
        invoice: Invoice,
        supplier: SupplierProfile,
        evaluation: StatutoryEvaluation,
        language: SupportedLanguage = SupportedLanguage.ENGLISH,
        buyer_portal_base_url: str = "https://dueclock.in/p"
    ) -> str:
        """Renders ready-to-dispatch WhatsApp message text."""
        lang_templates = cls.TEMPLATES.get(language, cls.TEMPLATES[SupportedLanguage.ENGLISH])
        template = lang_templates.get(stage, cls.TEMPLATES[SupportedLanguage.ENGLISH][stage])

        upi_link = UPILinkGenerator.generate_upi_link(
            vpa=supplier.upi_vpa,
            payee_name=supplier.trade_name or supplier.business_name,
            amount=invoice.outstanding_amount,
            invoice_number=invoice.invoice_number
        )

        portal_url = f"{buyer_portal_base_url}/{invoice.id}"

        return template.format(
            buyer_name=invoice.buyer.buyer_name,
            supplier_name=supplier.trade_name or supplier.business_name,
            invoice_number=invoice.invoice_number,
            amount=invoice.outstanding_amount,
            due_date=evaluation.statutory_due_date.strftime("%d-%b-%Y"),
            statutory_rate=f"{evaluation.annual_interest_rate_pct:.1f}",
            accrued_interest=evaluation.accrued_statutory_interest,
            upi_link=upi_link,
            buyer_portal_url=portal_url
        )
