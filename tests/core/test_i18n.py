import re
import string
from datetime import date

import pytest

from dueclock.core import i18n
from dueclock.core.buyer_page import DISPUTE_REASONS, PageData, fmt_date, render_invoice_page
from dueclock.core.templates import TEMPLATES, template_name
from tests.core.test_buyer_page import Env, REC  # noqa: F401  (reuse the buyer-page environment)

DEVANAGARI, KANNADA = re.compile(r"[ऀ-ॿ]"), re.compile(r"[ಀ-೿]")
fmt = string.Formatter()
fields = lambda s: sorted(n for _, n, _, _ in fmt.parse(s) if n)


def test_every_string_exists_in_all_languages_with_identical_placeholders():
    for key, trio in i18n._S.items():
        assert len(trio) == 3 and all(x.strip() for x in trio), key
        assert len({tuple(fields(x)) for x in trio}) == 1, f"{key}: placeholders differ between languages"


def test_hindi_and_kannada_text_really_is_in_those_scripts():
    for key, (en, hi, kn) in i18n._S.items():
        assert DEVANAGARI.search(hi) and not KANNADA.search(hi), key
        assert KANNADA.search(kn) and not DEVANAGARI.search(kn), key
    for r, (en, hi, kn) in i18n._REASONS.items():
        assert DEVANAGARI.search(hi) and KANNADA.search(kn), r
    assert set(i18n._REASONS) == set(DISPUTE_REASONS)                       # every stored reason has a label


def test_whatsapp_bodies_match_the_variables_the_code_sends():
    assert set(i18n.WA_BODIES) == set(TEMPLATES)
    for step, bodies in i18n.WA_BODIES.items():
        want = list(range(1, len(TEMPLATES[step].vars) + 1))
        for lang in i18n.LANGS:
            body = bodies[lang]
            assert sorted(int(n) for n in set(re.findall(r"\{\{(\d)\}\}", body))) == want, f"{step}/{lang}: variables {want} expected"
            assert not body.startswith("{{") and not body.rstrip(". ").endswith("}}")      # Meta rejects a body that starts or ends with a variable
    assert template_name("S5", "hi") == "dc_s5_overdue_30_hi" and len(TEMPLATES["S5"].vars) == 4


def test_reviewed_languages_default_to_english_only():
    assert i18n.REVIEWED_LANGUAGES == ("en",)                               # hi/kn stay 'draft' until a native speaker signs off


def test_localized_dates_and_unknown_language_falls_back():
    d = date(2027, 4, 5)
    assert fmt_date(d) == "5 Apr 2027" and fmt_date(d, "hi") == "5 अप्रैल 2027" and fmt_date(d, "kn") == "5 ಏಪ್ರಿ 2027" and fmt_date(d, "fr") == "5 Apr 2027"
    assert i18n.t("xx", "settled_h") == i18n.t("en", "settled_h")


def page(lang, **kw):
    base = dict(token="tok", today=date(2027, 1, 20), seller_name="Precision Metal Works", invoice_number="INV/1", invoice_date=date(2027, 1, 1), amount=100000, outstanding=100000,
                due_date=date(2027, 1, 16), days_remaining=-4, overdue_days=4, status="OPEN", statutory_ok=True, fy_label="FY 2026-27", fy_end_year=2027, interest=216.67,
                upi_vpa="precision@icici", lang=lang)
    base.update(kw)
    return render_invoice_page(PageData(**base))


@pytest.mark.parametrize("lang,script,words", [("hi", DEVANAGARI, ("आपकी कर स्थिति", "UPI से", "मैं एक तारीख को भुगतान करूँगा")), ("kn", KANNADA, ("ನಿಮ್ಮ ತೆರಿಗೆ ಸ್ಥಿತಿ", "UPI ಮೂಲಕ", "ನಾನು ಒಂದು ದಿನಾಂಕದಂದು"))])
def test_buyer_page_renders_fully_in_the_language(lang, script, words):
    h = page(lang)
    assert f'<html lang="{lang}">' in h and all(w in h for w in words)
    assert "Your tax position" not in h and "Tell the supplier" not in h and "Check my exposure" not in h        # no English left over in the body text
    assert "₹1,00,000" in h and "₹216.67" in h                                                              # amounts keep Indian grouping
    assert f"?lang={lang}" in h                                                                              # forms keep the language
    assert '<option value="Goods not received">' in h and script.search(re.search(r'<option value="Goods not received">([^<]+)<', h).group(1))   # label localized, stored value English


def test_english_page_is_unchanged_in_wording_and_has_a_language_bar():
    h = page("en")
    for w in ("Your tax position", "Pay ₹1,00,000.00 by UPI", "I will pay on a date", "Tell the supplier", "Was due on 16 Jan 2027 (4 days overdue)", "DueClock never holds your money"):
        assert w in h
    assert "English" in h and "हिन्दी" in h and "ಕನ್ನಡ" in h and 'href="/p/tok?lang=hi"' in h and 'href="/p/tok?lang=kn"' in h


def test_pluralisation_and_paid_state():
    assert "(1 day overdue)" in page("en", overdue_days=1) and "(in 1 day)" in page("en", overdue_days=0, days_remaining=1) and "Due today" in page("en", overdue_days=0, days_remaining=0)
    assert "चुकता" in page("hi", status="PAID") and "ಪಾವತಿಯಾಗಿದೆ" in page("kn", status="PAID")


def test_seller_names_are_still_escaped_in_every_language():
    for lang in i18n.LANGS:
        h = page(lang, seller_name='<script>alert(1)</script> & Co', invoice_number='"><img src=x>')
        assert "<script>alert" not in h and "<img src=x>" not in h and "&lt;script&gt;" in h


def test_end_to_end_language_choice_and_forms(tmp_path):
    env = Env(tmp_path)
    tok = env.link()
    en = env.get(tok).text
    assert "Your tax position" in en                                                                         # default English
    hi = env.get(tok, params={"lang": "hi"})
    assert hi.status_code == 200 and "आपकी कर स्थिति" in hi.text and "default-src 'none'" in hi.headers["content-security-policy"]
    assert 'lang="kn"' in env.get(tok, params={"lang": "kn"}).text and 'lang="en"' in env.get(tok, params={"lang": "zz"}).text      # junk falls back
    # the saved buyer language is used when no ?lang= is given
    bid = env.c.get("/v1/invoices", headers=env.h).json()["invoices"][0]["buyer_id"]
    assert env.c.put(f"/v1/buyers/{bid}/language", json={"language": "kn"}, headers=env.h).status_code == 200
    assert 'lang="kn"' in env.get(tok).text and 'lang="hi"' in env.get(tok, params={"lang": "hi"}).text          # explicit choice still wins
    # promise in Hindi: error pages and the redirect stay Hindi
    bad = env.c.post(f"/p/{tok}/promise?lang=hi", content="date=2020-01-01", headers={"content-type": "application/x-www-form-urlencoded"})
    assert bad.status_code == 400 and "यह तारीख मान्य नहीं है" in bad.text
    ok = env.c.post(f"/p/{tok}/promise?lang=hi", content="date=2027-02-01", headers={"content-type": "application/x-www-form-urlencoded"})
    assert ok.status_code == 303 and ok.headers["location"].endswith("?done=promise&lang=hi")
    done = env.c.get(ok.headers["location"]).text
    assert "हमने आपकी दी हुई तारीख दर्ज कर ली है" in done
    # dispute: an unknown reason is refused (in Kannada), then a valid one works and the redirect keeps Kannada
    bad_reason = env.c.post(f"/p/{tok}/dispute?lang=kn", content="reason=nonsense", headers={"content-type": "application/x-www-form-urlencoded"})
    assert bad_reason.status_code == 400 and "ದಯವಿಟ್ಟು ಕಾರಣ ಆಯ್ಕೆಮಾಡಿ" in bad_reason.text
    d = env.c.post(f"/p/{tok}/dispute?lang=kn", content="reason=Quality+issue&note=x", headers={"content-type": "application/x-www-form-urlencoded"})
    assert d.status_code == 303 and d.headers["location"].endswith("?done=dispute&lang=kn")
    assert "ನಿಮ್ಮ ವಿವಾದ ದಾಖಲಾಗಿದೆ" in env.c.get(d.headers["location"]).text
    assert env.c.get("/p/not-a-token?lang=hi").status_code == 404 and "यह लिंक मान्य नहीं है" in env.c.get("/p/not-a-token?lang=hi").text
