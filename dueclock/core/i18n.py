"""Buyer-page and WhatsApp-template text in English, Hindi and Kannada.

IMPORTANT: the Hindi and Kannada text was DRAFTED BY THE SOFTWARE AUTHOR, not by a native-speaking professional. It must be reviewed
by a native speaker (and, for the tax and MSMED sentences, by the CA reviewer) before it is shown to customers or submitted to Meta.
``REVIEWED_LANGUAGES`` lists the languages signed off; until a language is in it the owner screen marks it 'draft'.
"""
from __future__ import annotations

from typing import Dict, Tuple

LANGS = ("en", "hi", "kn")
LANG_NAMES = {"en": "English", "hi": "हिन्दी", "kn": "ಕನ್ನಡ"}
REVIEWED_LANGUAGES: Tuple[str, ...] = ("en",)          # add "hi" / "kn" only after native-speaker review

MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "hi": ("जन॰", "फ़र॰", "मार्च", "अप्रैल", "मई", "जून", "जुला॰", "अग॰", "सित॰", "अक्टू॰", "नव॰", "दिस॰"),
    "kn": ("ಜನ", "ಫೆಬ್ರ", "ಮಾರ್ಚ್", "ಏಪ್ರಿ", "ಮೇ", "ಜೂನ್", "ಜುಲೈ", "ಆಗ", "ಸೆಪ್ಟೆಂ", "ಅಕ್ಟೋ", "ನವೆಂ", "ಡಿಸೆಂ"),
}

# key -> (en, hi, kn). {placeholders} are filled with str.format; every language must use the same placeholders (tested).
_S: Dict[str, Tuple[str, str, str]] = {
    "title": ("Invoice {inv} from {seller}", "{seller} का इनवॉइस {inv}", "{seller} ಅವರ ಇನ್‌ವಾಯ್ಸ್ {inv}"),
    "settled_h": ("This invoice has been settled", "यह इनवॉइस चुकता हो चुका है", "ಈ ಇನ್‌ವಾಯ್ಸ್ ಪಾವತಿಯಾಗಿದೆ"),
    "settled_b": ("Thank you. No further action is needed.", "धन्यवाद। आगे कुछ करने की आवश्यकता नहीं है।", "ಧನ್ಯವಾದಗಳು. ಇನ್ನೇನೂ ಮಾಡುವ ಅಗತ್ಯವಿಲ್ಲ."),
    "overdue_1": ("Was due on {due} ({n} day overdue)", "{due} को देय था ({n} दिन विलंबित)", "{due} ರಂದು ಪಾವತಿಸಬೇಕಿತ್ತು ({n} ದಿನ ತಡ)"),
    "overdue_n": ("Was due on {due} ({n} days overdue)", "{due} को देय था ({n} दिन विलंबित)", "{due} ರಂದು ಪಾವತಿಸಬೇಕಿತ್ತು ({n} ದಿನ ತಡ)"),
    "today": ("Due today ({due})", "आज देय है ({due})", "ಇಂದು ಪಾವತಿಸಬೇಕು ({due})"),
    "future_1": ("Due on {due} (in {n} day)", "{due} को देय ({n} दिन में)", "{due} ರಂದು ಪಾವತಿಸಬೇಕು ({n} ದಿನದಲ್ಲಿ)"),
    "future_n": ("Due on {due} (in {n} days)", "{due} को देय ({n} दिन में)", "{due} ರಂದು ಪಾವತಿಸಬೇಕು ({n} ದಿನಗಳಲ್ಲಿ)"),
    "r_supplier": ("Supplier", "आपूर्तिकर्ता", "ಪೂರೈಕೆದಾರರು"),
    "r_number": ("Invoice number", "इनवॉइस संख्या", "ಇನ್‌ವಾಯ್ಸ್ ಸಂಖ್ಯೆ"),
    "r_date": ("Invoice date", "इनवॉइस की तारीख", "ಇನ್‌ವಾಯ್ಸ್ ದಿನಾಂಕ"),
    "r_amount": ("Invoice amount", "इनवॉइस राशि", "ಇನ್‌ವಾಯ್ಸ್ ಮೊತ್ತ"),
    "r_balance": ("Balance due", "बकाया राशि", "ಬಾಕಿ ಮೊತ್ತ"),
    "tax_h": ("Your tax position", "आपकी कर स्थिति", "ನಿಮ್ಮ ತೆರಿಗೆ ಸ್ಥಿತಿ"),
    "tax_over": ("This bill is past its statutory payment limit. If it is still unpaid on 31 March {year}, it may not be deductible in {fy}. Please check with your CA.",
                 "यह बिल वैधानिक भुगतान सीमा पार कर चुका है। यदि यह 31 मार्च {year} तक भी अदा नहीं हुआ, तो यह {fy} में कटौती योग्य नहीं हो सकता। कृपया अपने सीए से परामर्श करें।",
                 "ಈ ಬಿಲ್ ಕಾನೂನುಬದ್ಧ ಪಾವತಿ ಮಿತಿಯನ್ನು ಮೀರಿದೆ. 31 ಮಾರ್ಚ್ {year} ರಂದು ಇನ್ನೂ ಪಾವತಿಯಾಗದಿದ್ದರೆ, ಇದು {fy} ರಲ್ಲಿ ಕಡಿತಕ್ಕೆ ಅರ್ಹವಾಗದಿರಬಹುದು. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಸಿಎ ಅವರನ್ನು ಸಂಪರ್ಕಿಸಿ."),
    "tax_future": ("If this bill is unpaid after {due}, it may not be deductible in {fy}. Please check with your CA.",
                   "यदि यह बिल {due} के बाद भी अदा नहीं हुआ, तो यह {fy} में कटौती योग्य नहीं हो सकता। कृपया अपने सीए से परामर्श करें।",
                   "ಈ ಬಿಲ್ {due} ನಂತರವೂ ಪಾವತಿಯಾಗದಿದ್ದರೆ, ಇದು {fy} ರಲ್ಲಿ ಕಡಿತಕ್ಕೆ ಅರ್ಹವಾಗದಿರಬಹುದು. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಸಿಎ ಅವರನ್ನು ಸಂಪರ್ಕಿಸಿ."),
    "interest": ("Interest under the MSMED Act, as computed today: {amt}.", "MSMED अधिनियम के अंतर्गत आज तक की गणना के अनुसार ब्याज: {amt}।", "MSMED ಕಾಯ್ದೆಯಡಿ ಇಂದಿನವರೆಗೆ ಲೆಕ್ಕಹಾಕಿದ ಬಡ್ಡಿ: {amt}."),
    "tax_note": ("Computed for your CA to review. This is not legal advice.", "आपके सीए की समीक्षा के लिए गणना की गई। यह कानूनी सलाह नहीं है।", "ನಿಮ್ಮ ಸಿಎ ಪರಿಶೀಲನೆಗಾಗಿ ಲೆಕ್ಕಹಾಕಲಾಗಿದೆ. ಇದು ಕಾನೂನು ಸಲಹೆಯಲ್ಲ."),
    "pay_h": ("Pay", "भुगतान करें", "ಪಾವತಿಸಿ"),
    "pay_btn": ("Pay {amt} by UPI", "UPI से {amt} चुकाएँ", "UPI ಮೂಲಕ {amt} ಪಾವತಿಸಿ"),
    "pay_note": ("Opens your UPI app. The payment goes straight to {seller} ({vpa}). DueClock never holds your money.",
                 "आपका UPI ऐप खुलेगा। भुगतान सीधे {seller} ({vpa}) को जाएगा। DueClock आपका पैसा कभी अपने पास नहीं रखता।",
                 "ನಿಮ್ಮ UPI ಆ್ಯಪ್ ತೆರೆಯುತ್ತದೆ. ಹಣ ನೇರವಾಗಿ {seller} ({vpa}) ಅವರಿಗೆ ಹೋಗುತ್ತದೆ. DueClock ನಿಮ್ಮ ಹಣವನ್ನು ಎಂದಿಗೂ ಇಟ್ಟುಕೊಳ್ಳುವುದಿಲ್ಲ."),
    "pay_ask": ("Please ask {seller} for their payment details.", "कृपया {seller} से उनके भुगतान विवरण पूछें।", "ದಯವಿಟ್ಟು {seller} ಅವರಿಂದ ಪಾವತಿ ವಿವರಗಳನ್ನು ಕೇಳಿ."),
    "disputed": ("A dispute has been raised on this invoice. The supplier will contact you.", "इस इनवॉइस पर विवाद दर्ज किया गया है। आपूर्तिकर्ता आपसे संपर्क करेगा।", "ಈ ಇನ್‌ವಾಯ್ಸ್‌ಗೆ ವಿವಾದ ದಾಖಲಾಗಿದೆ. ಪೂರೈಕೆದಾರರು ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸುತ್ತಾರೆ."),
    "promise_h": ("I will pay on a date", "मैं एक तारीख को भुगतान करूँगा", "ನಾನು ಒಂದು ದಿನಾಂಕದಂದು ಪಾವತಿಸುತ್ತೇನೆ"),
    "promise_told": ("You told us you will pay on {date}.", "आपने बताया है कि आप {date} को भुगतान करेंगे।", "ನೀವು {date} ರಂದು ಪಾವತಿಸುವುದಾಗಿ ತಿಳಿಸಿದ್ದೀರಿ."),
    "promise_label": ("Date you will pay", "भुगतान की तारीख", "ಪಾವತಿ ದಿನಾಂಕ"),
    "promise_btn": ("Confirm this date", "यह तारीख पक्की करें", "ಈ ದಿನಾಂಕವನ್ನು ದೃಢೀಕರಿಸಿ"),
    "dispute_h": ("There is a problem with this invoice", "इस इनवॉइस में समस्या है", "ಈ ಇನ್‌ವಾಯ್ಸ್‌ನಲ್ಲಿ ಸಮಸ್ಯೆ ಇದೆ"),
    "dispute_q": ("What is the problem?", "समस्या क्या है?", "ಸಮಸ್ಯೆ ಏನು?"),
    "dispute_notes": ("Details (optional)", "विवरण (वैकल्पिक)", "ವಿವರಗಳು (ಐಚ್ಛಿಕ)"),
    "dispute_btn": ("Tell the supplier", "आपूर्तिकर्ता को बताएँ", "ಪೂರೈಕೆದಾರರಿಗೆ ತಿಳಿಸಿ"),
    "scan_h": ("Check your own payables", "अपने देय बिल जाँचें", "ನಿಮ್ಮ ಬಾಕಿ ಬಿಲ್‌ಗಳನ್ನು ಪರಿಶೀಲಿಸಿ"),
    "scan_b": ("See which of your vendor bills may be past the statutory limit. Free.", "देखें कि आपके कौन-से विक्रेता बिल वैधानिक सीमा पार कर चुके हो सकते हैं। निःशुल्क।", "ನಿಮ್ಮ ಯಾವ ಪೂರೈಕೆದಾರರ ಬಿಲ್‌ಗಳು ಕಾನೂನುಬದ್ಧ ಮಿತಿ ಮೀರಿರಬಹುದು ಎಂದು ನೋಡಿ. ಉಚಿತ."),
    "scan_btn": ("Check my exposure", "मेरा जोखिम जाँचें", "ನನ್ನ ಅಪಾಯವನ್ನು ಪರಿಶೀಲಿಸಿ"),
    "footer": ("Sent on behalf of {seller} using DueClock. This page records that you opened it and any date or dispute you submit, only to help {seller} get paid correctly. {privacy} · Questions or deletion requests: {email}",
               "यह {seller} की ओर से DueClock के माध्यम से भेजा गया है। यह पृष्ठ केवल यह दर्ज करता है कि आपने इसे खोला और आपके द्वारा दी गई कोई भी तारीख या विवाद, ताकि {seller} को सही भुगतान मिल सके। {privacy} · प्रश्न या हटाने के अनुरोध: {email}",
               "ಇದನ್ನು {seller} ಅವರ ಪರವಾಗಿ DueClock ಮೂಲಕ ಕಳುಹಿಸಲಾಗಿದೆ. ನೀವು ಈ ಪುಟವನ್ನು ತೆರೆದಿರುವುದನ್ನು ಮತ್ತು ನೀವು ನೀಡುವ ದಿನಾಂಕ ಅಥವಾ ವಿವಾದವನ್ನು ಮಾತ್ರ ಇದು ದಾಖಲಿಸುತ್ತದೆ; {seller} ಅವರಿಗೆ ಸರಿಯಾದ ಪಾವತಿ ಸಿಗಲು ಮಾತ್ರ. {privacy} · ಪ್ರಶ್ನೆಗಳು ಅಥವಾ ಅಳಿಸುವ ವಿನಂತಿಗಳು: {email}"),
    "privacy": ("Privacy notice", "गोपनीयता सूचना", "ಗೌಪ್ಯತಾ ಸೂಚನೆ"),
    "n_promise": ("Thank you. We have noted the date you gave and will follow up only on that date.", "धन्यवाद। हमने आपकी दी हुई तारीख दर्ज कर ली है और केवल उसी तारीख को संपर्क करेंगे।", "ಧನ್ಯವಾದಗಳು. ನೀವು ನೀಡಿದ ದಿನಾಂಕವನ್ನು ದಾಖಲಿಸಿದ್ದೇವೆ; ಆ ದಿನಾಂಕದಂದು ಮಾತ್ರ ಅನುಸರಣೆ ಮಾಡುತ್ತೇವೆ."),
    "n_dispute": ("Your dispute has been recorded. The supplier will contact you.", "आपका विवाद दर्ज कर लिया गया है। आपूर्तिकर्ता आपसे संपर्क करेगा।", "ನಿಮ್ಮ ವಿವಾದ ದಾಖಲಾಗಿದೆ. ಪೂರೈಕೆದಾರರು ನಿಮ್ಮನ್ನು ಸಂಪರ್ಕಿಸುತ್ತಾರೆ."),
    "bad_date_t": ("That date is not valid", "यह तारीख मान्य नहीं है", "ಈ ದಿನಾಂಕ ಮಾನ್ಯವಲ್ಲ"),
    "bad_date_1": ("Please go back and choose a date.", "कृपया वापस जाकर तारीख चुनें।", "ದಯವಿಟ್ಟು ಹಿಂದೆ ಹೋಗಿ ದಿನಾಂಕ ಆಯ್ಕೆಮಾಡಿ."),
    "bad_date_2": ("Please choose a date within the next {n} days.", "कृपया अगले {n} दिनों के भीतर की कोई तारीख चुनें।", "ದಯವಿಟ್ಟು ಮುಂದಿನ {n} ದಿನಗಳೊಳಗಿನ ದಿನಾಂಕ ಆಯ್ಕೆಮಾಡಿ."),
    "bad_link_t": ("This link is not valid", "यह लिंक मान्य नहीं है", "ಈ ಲಿಂಕ್ ಮಾನ್ಯವಲ್ಲ"),
    "bad_link_b": ("It may have expired or been replaced. Please ask the supplier for a new link.", "यह समाप्त हो गया या बदल दिया गया हो सकता है। कृपया आपूर्तिकर्ता से नया लिंक माँगें।", "ಇದು ಮುಗಿದಿರಬಹುದು ಅಥವಾ ಬದಲಾಯಿಸಲ್ಪಟ್ಟಿರಬಹುದು. ದಯವಿಟ್ಟು ಪೂರೈಕೆದಾರರಿಂದ ಹೊಸ ಲಿಂಕ್ ಕೇಳಿ."),
    "many_t": ("Too many attempts", "बहुत अधिक प्रयास", "ತುಂಬಾ ಪ್ರಯತ್ನಗಳು"),
    "many_b": ("Please try again later.", "कृपया बाद में पुनः प्रयास करें।", "ದಯವಿಟ್ಟು ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ."),
    "reason_t": ("Please choose a reason", "कृपया कारण चुनें", "ದಯವಿಟ್ಟು ಕಾರಣ ಆಯ್ಕೆಮಾಡಿ"),
    "reason_b": ("Go back and pick what the problem is.", "वापस जाकर चुनें कि समस्या क्या है।", "ಹಿಂದೆ ಹೋಗಿ ಸಮಸ್ಯೆ ಏನೆಂದು ಆಯ್ಕೆಮಾಡಿ."),
}
_REASONS = {
    "Goods not received": ("Goods not received", "माल प्राप्त नहीं हुआ", "ಸರಕು ಬಂದಿಲ್ಲ"),
    "Quality issue": ("Quality issue", "गुणवत्ता की समस्या", "ಗುಣಮಟ್ಟದ ಸಮಸ್ಯೆ"),
    "Wrong amount": ("Wrong amount", "गलत राशि", "ತಪ್ಪು ಮೊತ್ತ"),
    "Already paid": ("Already paid", "पहले ही भुगतान किया जा चुका है", "ಈಗಾಗಲೇ ಪಾವತಿಸಲಾಗಿದೆ"),
    "Other": ("Other", "अन्य", "ಇತರೆ"),
}


def norm_lang(lang: str | None) -> str:
    return lang if lang in LANGS else "en"


def t(lang: str, key: str, **kw) -> str:
    text = _S[key][LANGS.index(norm_lang(lang))]
    return text.format(**kw) if kw else text


def reason_label(lang: str, reason: str) -> str:
    return _REASONS.get(reason, (reason,) * 3)[LANGS.index(norm_lang(lang))]


def month_abbr(lang: str, month: int) -> str:
    return MONTHS[norm_lang(lang)][month - 1]


# WhatsApp template bodies, Meta numbered-variable format: {{1}} buyer, {{2}} seller, {{3}} invoice, {{4}} amount, {{5}} date.
# English matches blueprint/15_WhatsApp_Template_Tracker.xlsx. Hindi and Kannada are unreviewed drafts (see module note).
WA_BODIES: Dict[str, Dict[str, str]] = {
    "S1": {"en": "Hello {{1}}, this is {{2}}. Invoice {{3}} for Rs {{4}} is due on {{5}}. You can view the invoice and pay by UPI using the button below. Thank you.",
           "hi": "नमस्ते {{1}}, यह {{2}} की ओर से है। इनवॉइस {{3}}, Rs {{4}}, की देय तारीख {{5}} है। इनवॉइस देखने और UPI से भुगतान करने के लिए नीचे दिए बटन का उपयोग करें। धन्यवाद।",
           "kn": "ನಮಸ್ಕಾರ {{1}}, ಇದು {{2}} ಅವರಿಂದ. ಇನ್‌ವಾಯ್ಸ್ {{3}}, Rs {{4}}, ಪಾವತಿ ದಿನಾಂಕ {{5}}. ಇನ್‌ವಾಯ್ಸ್ ನೋಡಲು ಮತ್ತು UPI ಮೂಲಕ ಪಾವತಿಸಲು ಕೆಳಗಿನ ಬಟನ್ ಬಳಸಿ. ಧನ್ಯವಾದಗಳು."},
    "S2": {"en": "Hello {{1}}, a reminder from {{2}}: invoice {{3}} for Rs {{4}} is due today, {{5}}. Please pay using the button below. Thank you.",
           "hi": "नमस्ते {{1}}, {{2}} की ओर से अनुस्मारक: इनवॉइस {{3}}, Rs {{4}}, आज {{5}} को देय है। कृपया नीचे दिए बटन से भुगतान करें। धन्यवाद।",
           "kn": "ನಮಸ್ಕಾರ {{1}}, {{2}} ಅವರಿಂದ ಜ್ಞಾಪನೆ: ಇನ್‌ವಾಯ್ಸ್ {{3}}, Rs {{4}}, ಇಂದು {{5}} ರಂದು ಪಾವತಿಸಬೇಕಿದೆ. ದಯವಿಟ್ಟು ಕೆಳಗಿನ ಬಟನ್ ಮೂಲಕ ಪಾವತಿಸಿ. ಧನ್ಯವಾದಗಳು."},
    "S3": {"en": "Hello {{1}}, invoice {{3}} from {{2}} for Rs {{4}} was due on {{5}} and is now 3 days overdue. Please share the payment date or pay using the button below. Thank you.",
           "hi": "नमस्ते {{1}}, {{2}} का इनवॉइस {{3}}, Rs {{4}}, {{5}} को देय था और अब 3 दिन विलंबित है। कृपया भुगतान की तारीख बताएँ या नीचे दिए बटन से भुगतान करें। धन्यवाद।",
           "kn": "ನಮಸ್ಕಾರ {{1}}, {{2}} ಅವರ ಇನ್‌ವಾಯ್ಸ್ {{3}}, Rs {{4}}, {{5}} ರಂದು ಪಾವತಿಸಬೇಕಿತ್ತು ಮತ್ತು ಈಗ 3 ದಿನ ತಡವಾಗಿದೆ. ದಯವಿಟ್ಟು ಪಾವತಿ ದಿನಾಂಕ ತಿಳಿಸಿ ಅಥವಾ ಕೆಳಗಿನ ಬಟನ್ ಮೂಲಕ ಪಾವತಿಸಿ. ಧನ್ಯವಾದಗಳು."},
    "S4": {"en": "Hello {{1}}, invoice {{3}} from {{2}} for Rs {{4}} is 10 days past its due date of {{5}}. Under the MSMED Act, payment to a micro or small supplier is due within the agreed period, at most 45 days. Please confirm the payment date using the button below. Thank you.",
           "hi": "नमस्ते {{1}}, {{2}} का इनवॉइस {{3}}, Rs {{4}}, अपनी देय तारीख {{5}} से 10 दिन आगे निकल चुका है। MSMED अधिनियम के अंतर्गत सूक्ष्म या लघु आपूर्तिकर्ता को भुगतान सहमत अवधि के भीतर, अधिकतम 45 दिनों में, देय होता है। कृपया नीचे दिए बटन से भुगतान की तारीख की पुष्टि करें। धन्यवाद।",
           "kn": "ನಮಸ್ಕಾರ {{1}}, {{2}} ಅವರ ಇನ್‌ವಾಯ್ಸ್ {{3}}, Rs {{4}}, ಪಾವತಿ ದಿನಾಂಕ {{5}} ಕ್ಕಿಂತ 10 ದಿನ ಮೀರಿದೆ. MSMED ಕಾಯ್ದೆಯ ಪ್ರಕಾರ ಸೂಕ್ಷ್ಮ ಅಥವಾ ಸಣ್ಣ ಪೂರೈಕೆದಾರರಿಗೆ ಪಾವತಿಯು ಒಪ್ಪಿದ ಅವಧಿಯೊಳಗೆ, ಗರಿಷ್ಠ 45 ದಿನಗಳೊಳಗೆ ಆಗಬೇಕು. ದಯವಿಟ್ಟು ಕೆಳಗಿನ ಬಟನ್ ಮೂಲಕ ಪಾವತಿ ದಿನಾಂಕವನ್ನು ದೃಢೀಕರಿಸಿ. ಧನ್ಯವಾದಗಳು."},
    "S5": {"en": "Hello {{1}}, invoice {{3}} from {{2}} for Rs {{4}} is now 30 days overdue. Please pay, or tell us a date you can commit to, using the button below. If there is a dispute, please let us know. Thank you.",
           "hi": "नमस्ते {{1}}, {{2}} का इनवॉइस {{3}}, Rs {{4}}, अब 30 दिन विलंबित है। कृपया नीचे दिए बटन से भुगतान करें या कोई तारीख बताएँ जिस पर आप भुगतान कर सकें। यदि कोई विवाद है तो कृपया हमें बताएँ। धन्यवाद।",
           "kn": "ನಮಸ್ಕಾರ {{1}}, {{2}} ಅವರ ಇನ್‌ವಾಯ್ಸ್ {{3}}, Rs {{4}}, ಈಗ 30 ದಿನ ತಡವಾಗಿದೆ. ದಯವಿಟ್ಟು ಕೆಳಗಿನ ಬಟನ್ ಮೂಲಕ ಪಾವತಿಸಿ, ಅಥವಾ ನೀವು ಪಾವತಿಸಬಹುದಾದ ದಿನಾಂಕ ತಿಳಿಸಿ. ವಿವಾದವಿದ್ದರೆ ದಯವಿಟ್ಟು ತಿಳಿಸಿ. ಧನ್ಯವಾದಗಳು."},
    "P1": {"en": "Hello {{1}}, this is a reminder from {{2}}: you told us invoice {{3}} for Rs {{4}} would be paid on {{5}}. Please pay today using the button below. Thank you.",
           "hi": "नमस्ते {{1}}, {{2}} की ओर से अनुस्मारक: आपने बताया था कि इनवॉइस {{3}}, Rs {{4}}, का भुगतान {{5}} को होगा। कृपया आज नीचे दिए बटन से भुगतान करें। धन्यवाद।",
           "kn": "ನಮಸ್ಕಾರ {{1}}, {{2}} ಅವರಿಂದ ಜ್ಞಾಪನೆ: ಇನ್‌ವಾಯ್ಸ್ {{3}}, Rs {{4}}, {{5}} ರಂದು ಪಾವತಿಯಾಗುತ್ತದೆ ಎಂದು ನೀವು ತಿಳಿಸಿದ್ದಿರಿ. ದಯವಿಟ್ಟು ಇಂದು ಕೆಳಗಿನ ಬಟನ್ ಮೂಲಕ ಪಾವತಿಸಿ. ಧನ್ಯವಾದಗಳು."},
    "S6": {"en": "Hello {{1}}, thank you for confirming. {{2}} has noted that invoice {{3}} for Rs {{4}} will be paid on {{5}}. We will follow up only on that date. Thank you.",
           "hi": "नमस्ते {{1}}, पुष्टि के लिए धन्यवाद। {{2}} ने दर्ज किया है कि इनवॉइस {{3}}, Rs {{4}}, का भुगतान {{5}} को किया जाएगा। हम केवल उसी तारीख को संपर्क करेंगे। धन्यवाद।",
           "kn": "ನಮಸ್ಕಾರ {{1}}, ದೃಢೀಕರಿಸಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು. ಇನ್‌ವಾಯ್ಸ್ {{3}}, Rs {{4}}, {{5}} ರಂದು ಪಾವತಿಯಾಗುತ್ತದೆ ಎಂದು {{2}} ದಾಖಲಿಸಿದ್ದಾರೆ. ಆ ದಿನಾಂಕದಂದು ಮಾತ್ರ ನಾವು ಸಂಪರ್ಕಿಸುತ್ತೇವೆ. ಧನ್ಯವಾದಗಳು."},
    "S7": {"en": "Hello {{1}}, we have received your payment for invoice {{3}} of Rs {{4}} to {{2}}. Thank you. No further reminders will be sent for this invoice.",
           "hi": "नमस्ते {{1}}, {{2}} को इनवॉइस {{3}}, Rs {{4}}, का आपका भुगतान प्राप्त हो गया है। धन्यवाद। इस इनवॉइस के लिए अब कोई अनुस्मारक नहीं भेजा जाएगा।",
           "kn": "ನಮಸ್ಕಾರ {{1}}, {{2}} ಅವರಿಗೆ ನೀವು ಮಾಡಿದ ಇನ್‌ವಾಯ್ಸ್ {{3}} (Rs {{4}}) ಪಾವತಿ ಸ್ವೀಕೃತವಾಗಿದೆ. ಧನ್ಯವಾದಗಳು. ಈ ಇನ್‌ವಾಯ್ಸ್‌ಗೆ ಇನ್ನು ಮುಂದೆ ಜ್ಞಾಪನೆ ಕಳುಹಿಸುವುದಿಲ್ಲ."},
}
