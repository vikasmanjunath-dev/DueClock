"""Reminder planner: decide whether a reminder is due, which one, and why not otherwise.

Five steps relative to the statutory due date (PRD F3, decision D-20 pending): due-5, due day, +3, +10, +30.
Rules: only covered, outstanding invoices; opt-in required; stop on payment, dispute or pause; one message per buyer
per day; quiet hours 09:00-19:00; if several steps were missed, send only the latest one due; follow up on a
promised date, not before it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from .clock import ClockResult

STEPS = (("S1", -5), ("S2", 0), ("S3", 3), ("S4", 10), ("S5", 30))
PROMISE_STEP = "P1"
QUIET_START_HOUR, QUIET_END_HOUR = 9, 19
DAILY_CAP_PER_BUYER = 1


@dataclass(frozen=True)
class Decision:
    action: str                       # send | wait | none
    step: Optional[str]
    reason: str
    resume_on: Optional[date] = None


def plan(
    result: ClockResult,
    sent_steps: Iterable[str],
    *,
    opted_in: bool,
    now: datetime,
    paused: bool = False,
    disputed: bool = False,
    promised_date: Optional[date] = None,
    sent_today_to_buyer: int = 0,
) -> Decision:
    sent = set(sent_steps)
    today = now.date()
    if result.outstanding <= 0:
        return Decision("none", None, "PAID")
    if not result.covered:
        return Decision("none", None, "NOT_CLOCKED")
    if disputed:
        return Decision("none", None, "DISPUTED")
    if paused:
        return Decision("none", None, "PAUSED")
    if not opted_in:
        return Decision("none", None, "NO_OPT_IN")

    step: Optional[str] = None
    if promised_date is not None and promised_date >= today:
        if promised_date > today:
            return Decision("wait", None, "PROMISE_PENDING", promised_date)
        if PROMISE_STEP not in sent:
            step = PROMISE_STEP
    if step is None:
        since_due = -result.days_remaining
        due_steps = [s for s, off in STEPS if off <= since_due and s not in sent]
        if not due_steps:
            upcoming = [(s, off) for s, off in STEPS if s not in sent and off > since_due]
            nxt = result.due_date + timedelta(days=upcoming[0][1]) if upcoming else None
            return Decision("none", None, "NO_STEP_DUE", nxt)
        step = due_steps[-1]

    if not (QUIET_START_HOUR <= now.hour < QUIET_END_HOUR):
        resume = today if now.hour < QUIET_START_HOUR else today + timedelta(days=1)
        return Decision("wait", step, "QUIET_HOURS", resume)
    if sent_today_to_buyer >= DAILY_CAP_PER_BUYER:
        return Decision("wait", step, "DAILY_CAP", today + timedelta(days=1))
    return Decision("send", step, "DUE")
