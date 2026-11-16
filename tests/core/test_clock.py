"""Statutory test pack: worked examples from the QA Plan (E1-E10) plus edge cases.

Expected values are computed from the rules in the PRD. They await sign-off by a domain reviewer (a CA);
the OPEN questions (acceptance date, registration date) are asserted only as review flags.
"""
from datetime import date

import pytest

from dueclock.core.clock import (
    InvoiceFacts, RatePolicy, SupplierFacts, assess_coverage, Coverage, compound_interest, evaluate,
    financial_year, financial_year_end, year_end_exposure,
)
from dueclock.core.rates import RateEntry, RateTable

COVERED = SupplierFacts("MICRO", "MANUFACTURING", date(2024, 1, 10))
TRADER = SupplierFacts("SMALL", "TRADING", date(2024, 1, 10))
D1 = date(2027, 1, 1)
FLAT_6_5 = RateTable([RateEntry(date(2000, 1, 1), 6.5, True)])          # the 6.5% flat rate the worked examples were written against


def inv(**kw):
    base = dict(invoice_date=D1, delivery_date=D1, outstanding=100000.0, has_written_agreement=False, agreed_credit_days=0)
    base.update(kw)
    return InvoiceFacts(**base)


def test_e1_no_agreement_is_15_days():
    r = evaluate(inv(), COVERED, date(2027, 1, 5))
    assert r.due_date == date(2027, 1, 16) and r.statutory_cap_days == 15 and r.effective_days == 15
    assert r.days_remaining == 11 and not r.is_overdue


def test_e2_agreement_45_days():
    r = evaluate(inv(has_written_agreement=True, agreed_credit_days=45), COVERED, date(2027, 1, 5))
    assert r.due_date == date(2027, 2, 15) and r.statutory_cap_days == 45


def test_e3_agreement_above_45_is_capped_and_flagged():
    r = evaluate(inv(has_written_agreement=True, agreed_credit_days=60), COVERED, date(2027, 1, 5))
    assert r.due_date == date(2027, 2, 15) and "AGREEMENT_CAPPED" in r.review_flags


def test_agreement_shorter_than_45_is_respected():
    r = evaluate(inv(has_written_agreement=True, agreed_credit_days=30), COVERED, date(2027, 1, 5))
    assert r.due_date == date(2027, 1, 31)


def test_agreement_without_days_falls_back_to_15():
    r = evaluate(inv(has_written_agreement=True, agreed_credit_days=0), COVERED, date(2027, 1, 5))
    assert r.due_date == date(2027, 1, 16) and "AGREEMENT_DAYS_INVALID" in r.review_flags


def test_e4_trader_is_not_clocked():
    r = evaluate(inv(agreed_credit_days=30), TRADER, date(2027, 3, 1))
    assert not r.covered and r.coverage == "NOT_COVERED_TRADER" and r.due_date == date(2027, 1, 31)
    assert r.interest == 0.0 and not r.disallowance_exposure and r.statutory_cap_days is None


def test_e5_interest_one_month_plus_28_days():
    r = evaluate(inv(), COVERED, date(2027, 3, 15), FLAT_6_5)
    assert r.overdue_days == 58
    assert r.interest == pytest.approx(3166.31, abs=0.01) and r.annual_interest_rate_pct == pytest.approx(19.5)


def test_e6_interest_exactly_one_month():
    assert compound_interest(100000, 30, 19.5) == 1625.00


def test_e7_interest_on_outstanding_balance_only():
    r = evaluate(inv(outstanding=60000.0), COVERED, date(2027, 2, 15), FLAT_6_5)
    assert r.overdue_days == 30 and r.interest == 975.00


def test_e8_year_end_exposure_flagged():
    r = evaluate(inv(invoice_date=date(2027, 2, 1), delivery_date=date(2027, 2, 1), has_written_agreement=True, agreed_credit_days=45), COVERED, date(2027, 3, 31))
    assert r.due_date == date(2027, 3, 18) and r.overdue_days == 13 and r.disallowance_exposure and r.financial_year == "FY 2026-27"


def test_e9_invoice_due_after_year_end_has_no_exposure():
    r = evaluate(inv(invoice_date=date(2027, 3, 20), delivery_date=date(2027, 3, 20)), COVERED, date(2027, 3, 31))
    assert r.due_date == date(2027, 4, 4) and not r.is_overdue and not r.disallowance_exposure


def test_e10_rate_policy_changes_interest_when_rate_moves():
    table = RateTable([RateEntry(date(2020, 1, 1), 6.5, True), RateEntry(date(2027, 2, 1), 6.0, True)])
    at_due = evaluate(inv(), COVERED, date(2027, 3, 17), table, rate_policy=RatePolicy.AT_DUE_DATE)
    through = evaluate(inv(), COVERED, date(2027, 3, 17), table, rate_policy=RatePolicy.THROUGH_PERIOD)
    assert at_due.overdue_days == 60
    assert at_due.interest == pytest.approx(100000 * ((1 + 0.195 / 12) ** 2 - 1), abs=0.01)
    assert through.interest == pytest.approx(100000 * ((1 + 0.195 / 12) * (1 + 0.18 / 12) - 1), abs=0.01)
    assert through.interest < at_due.interest


def test_open_question_acceptance_date_is_flagged_not_used():
    f = inv(acceptance_date=date(2027, 1, 5))
    r = evaluate(f, COVERED, date(2027, 1, 10))
    assert r.due_date == date(2027, 1, 16) and "ACCEPTANCE_DATE_IGNORED" in r.review_flags
    r2 = evaluate(f, COVERED, date(2027, 1, 10), use_acceptance_date=True)
    assert r2.due_date == date(2027, 1, 20)


def test_open_question_registration_after_supply_is_flagged():
    late = SupplierFacts("MICRO", "MANUFACTURING", date(2027, 6, 1))
    r = evaluate(inv(), late, date(2027, 1, 20))
    assert r.covered and "REGISTERED_AFTER_SUPPLY" in r.review_flags
    assert "REGISTRATION_DATE_MISSING" in evaluate(inv(), SupplierFacts("MICRO", "SERVICES"), date(2027, 1, 20)).review_flags


def test_fully_paid_invoice_never_overdue():
    r = evaluate(inv(outstanding=0.0), COVERED, date(2027, 6, 1))
    assert not r.is_overdue and r.interest == 0.0 and not r.disallowance_exposure


def test_coverage_matrix():
    assert assess_coverage(SupplierFacts("MICRO", "SERVICES")) == Coverage.COVERED
    assert assess_coverage(SupplierFacts("SMALL", "MANUFACTURING")) == Coverage.COVERED
    assert assess_coverage(SupplierFacts("MEDIUM", "MANUFACTURING")) == Coverage.NOT_COVERED_SIZE
    assert assess_coverage(SupplierFacts("MICRO", "TRADING")) == Coverage.NOT_COVERED_TRADER
    assert assess_coverage(SupplierFacts("UNKNOWN", "UNKNOWN")) == Coverage.UNKNOWN
    assert "SUPPLIER_PROFILE_INCOMPLETE" in evaluate(inv(), SupplierFacts("UNKNOWN", "UNKNOWN"), D1).review_flags


def test_default_rate_table_is_flagged_unconfirmed():
    assert "RATE_TABLE_UNCONFIRMED" in evaluate(inv(), COVERED, D1).review_flags
    assert "RATE_TABLE_UNCONFIRMED" not in evaluate(inv(), COVERED, D1, RateTable([RateEntry(date(2020, 1, 1), 6.5, True)])).review_flags


def test_financial_year_boundaries():
    assert financial_year(date(2027, 3, 31)) == "FY 2026-27" and financial_year(date(2027, 4, 1)) == "FY 2027-28"
    assert financial_year_end(date(2027, 2, 1)) == date(2027, 3, 31) and financial_year_end(date(2027, 4, 1)) == date(2028, 3, 31)


def test_year_end_exposure_helper():
    f = inv(invoice_date=date(2027, 2, 1), delivery_date=date(2027, 2, 1), has_written_agreement=True, agreed_credit_days=45)
    assert year_end_exposure(f, COVERED, date(2027, 2, 20)).disallowance_exposure


def test_result_is_deterministic_and_serialisable():
    a = evaluate(inv(), COVERED, date(2027, 2, 1)).to_dict()
    b = evaluate(inv(), COVERED, date(2027, 2, 1)).to_dict()
    assert a == b and a["due_date"] == "2027-01-16" and a["engine_version"].startswith("clock-")
    assert a["trace"]


def test_rate_table_lookup():
    t = RateTable([RateEntry(date(2024, 4, 1), 6.5), RateEntry(date(2025, 6, 1), 6.0)])
    assert t.rate_on(date(2023, 1, 1)) == 6.5 and t.rate_on(date(2025, 5, 31)) == 6.5 and t.rate_on(date(2025, 6, 1)) == 6.0
    with pytest.raises(ValueError):
        RateTable([])


# ---- RBI Bank Rate history ----
def test_rbi_history_known_points():
    t = RateTable.default()
    for d, want in [(date(2018, 1, 1), 6.25), (date(2019, 10, 4), 5.40), (date(2020, 3, 26), 5.40), (date(2020, 3, 27), 4.65), (date(2020, 5, 22), 4.25),
                    (date(2022, 5, 3), 4.25), (date(2022, 5, 4), 4.65), (date(2022, 12, 7), 6.50), (date(2023, 4, 1), 6.75), (date(2025, 2, 6), 6.75),
                    (date(2025, 2, 7), 6.50), (date(2025, 6, 6), 5.75), (date(2025, 12, 5), 5.50), (date(2026, 10, 6), 5.50), (date(2026, 10, 7), 5.75)]:
        assert t.rate_on(d) == want, d


def test_rbi_history_is_ordered_and_sane():
    from dueclock.core.rates import BANK_RATE_HISTORY
    ds = [r[0] for r in BANK_RATE_HISTORY]
    assert ds == sorted(ds) and len(set(ds)) == len(ds)
    steps = [round(b[1] - a[1], 2) for a, b in zip(BANK_RATE_HISTORY, BANK_RATE_HISTORY[1:])]
    assert max(map(abs, steps)) <= 0.75                                              # largest real move: 75 bp (Mar 2020)
    assert all(0 < r[1] < 10 for r in BANK_RATE_HISTORY)


def test_rbi_history_unconfirmed_and_staleness_flags():
    t = RateTable.default()
    assert not t.fully_confirmed
    assert "RATE_TABLE_STALE" not in evaluate(inv(), COVERED, date(2026, 12, 1)).review_flags
    assert "RATE_TABLE_STALE" in evaluate(inv(), COVERED, date(2027, 2, 1)).review_flags
    fresh = RateTable([RateEntry(date(2020, 1, 1), 6.5, True)])
    assert not fresh.is_stale(date(2030, 1, 1))                                      # no verified_through -> never claims staleness


def test_rate_in_force_at_due_date_vs_through_period():
    from dueclock.core.clock import RatePolicy
    # due 2025-05-20; the 6 June 2025 cut (bank rate 6.25% -> 5.75%) falls inside the overdue period
    i = inv(invoice_date=date(2025, 5, 5), delivery_date=date(2025, 5, 5))
    at_due = evaluate(i, COVERED, date(2025, 7, 19))
    through = evaluate(i, COVERED, date(2025, 7, 19), rate_policy=RatePolicy.THROUGH_PERIOD)
    assert at_due.due_date == date(2025, 5, 20) and at_due.overdue_days == 60
    assert at_due.annual_interest_rate_pct == pytest.approx(18.75)                  # 3 x 6.25, the rate on the due date
    assert at_due.interest == pytest.approx(compound_interest(100000, 60, 18.75), abs=0.01)
    assert 0 < through.interest < at_due.interest                                   # second month priced at 3 x 5.75
