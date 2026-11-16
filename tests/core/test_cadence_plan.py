from datetime import date, datetime

from dueclock.core.cadence_plan import Decision, plan
from dueclock.core.clock import InvoiceFacts, SupplierFacts, evaluate

SUP = SupplierFacts("MICRO", "MANUFACTURING", date(2024, 1, 1))
D1 = date(2027, 1, 1)            # due 16 Jan with no agreement


def res(as_of, outstanding=100000.0, sup=SUP):
    return evaluate(InvoiceFacts(invoice_date=D1, delivery_date=D1, outstanding=outstanding), sup, as_of)


def go(as_of, hour=10, sent=(), **kw):
    kw.setdefault("opted_in", True)
    return plan(res(as_of), sent, now=datetime(as_of.year, as_of.month, as_of.day, hour), **kw)


def test_stop_conditions():
    assert go(date(2027, 1, 20), opted_in=True).action == "send"
    assert plan(res(date(2027, 1, 20), 0), [], opted_in=True, now=datetime(2027, 1, 20, 10)).reason == "PAID"
    assert plan(res(date(2027, 1, 20), sup=SupplierFacts("MICRO", "TRADING")), [], opted_in=True, now=datetime(2027, 1, 20, 10)).reason == "NOT_CLOCKED"
    assert go(date(2027, 1, 20), disputed=True).reason == "DISPUTED"
    assert go(date(2027, 1, 20), paused=True).reason == "PAUSED"
    assert go(date(2027, 1, 20), opted_in=False).reason == "NO_OPT_IN"


def test_step_timing():
    assert go(date(2027, 1, 10)).step is None                                     # 6 days before due: nothing yet
    assert go(date(2027, 1, 10)).resume_on == date(2027, 1, 11)
    assert go(date(2027, 1, 11)).step == "S1"                                     # due minus 5
    assert go(date(2027, 1, 16)).step == "S2"
    assert go(date(2027, 1, 19)).step == "S3"
    assert go(date(2027, 1, 26)).step == "S4"
    assert go(date(2027, 2, 15)).step == "S5"


def test_missed_steps_send_only_the_latest():
    d = go(date(2027, 1, 28))                                                     # 12 days overdue, nothing sent
    assert d.action == "send" and d.step == "S4"
    assert go(date(2027, 1, 28), sent=["S1", "S2", "S3", "S4"]).action == "none"


def test_quiet_hours_and_daily_cap():
    early = go(date(2027, 1, 19), hour=8)
    assert (early.action, early.reason, early.resume_on) == ("wait", "QUIET_HOURS", date(2027, 1, 19))
    late = go(date(2027, 1, 19), hour=20)
    assert (late.reason, late.resume_on) == ("QUIET_HOURS", date(2027, 1, 20))
    assert go(date(2027, 1, 19), hour=19).reason == "QUIET_HOURS" and go(date(2027, 1, 19), hour=9).action == "send"
    assert go(date(2027, 1, 19), sent_today_to_buyer=1).reason == "DAILY_CAP"


def test_promise_date_handling():
    assert go(date(2027, 1, 20), promised_date=date(2027, 1, 25)).reason == "PROMISE_PENDING"
    d = go(date(2027, 1, 25), promised_date=date(2027, 1, 25))
    assert d.action == "send" and d.step == "P1"
    after = go(date(2027, 1, 25), promised_date=date(2027, 1, 25), sent=["P1"])
    assert after.action == "send" and after.step == "S3"                          # promise day handled; normal cadence continues
    missed = go(date(2027, 1, 28), promised_date=date(2027, 1, 25))
    assert missed.step == "S4"                                                    # promise broken: normal cadence resumes
