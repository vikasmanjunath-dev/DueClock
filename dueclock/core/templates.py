"""WhatsApp template registry. Names and variable order match the Template Tracker (blueprint/15_*).

Variables are numbered in the order of ``vars``: {{1}} buyer, {{2}} seller, {{3}} invoice number, {{4}} amount (no
currency sign, the template says 'Rs'), {{5}} date. The 'Pay' button is a dynamic URL whose suffix is the buyer-page token.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Tuple

from .buyer_page import fmt_date, inr

DEFAULT_LANGUAGE = "en"


@dataclass(frozen=True)
class TemplateDef:
    key: str
    base: str
    vars: Tuple[str, ...]
    button: bool = True


FULL = ("buyer", "seller", "invoice", "amount", "date")
TEMPLATES = {
    "S1": TemplateDef("S1", "dc_s1_heads_up", FULL),
    "S2": TemplateDef("S2", "dc_s2_due_day", FULL),
    "S3": TemplateDef("S3", "dc_s3_overdue_3", FULL),
    "S4": TemplateDef("S4", "dc_s4_overdue_10", FULL),
    "S5": TemplateDef("S5", "dc_s5_overdue_30", ("buyer", "seller", "invoice", "amount")),     # the approved body has no date variable
    "P1": TemplateDef("P1", "dc_p1_promise_followup", FULL),
    "S6": TemplateDef("S6", "dc_s6_promise_confirmation", FULL, button=False),
    "S7": TemplateDef("S7", "dc_s7_payment_received", ("buyer", "seller", "invoice", "amount"), button=False),
}


def step_key(step: str) -> str:
    """'S6:2027-01-25' -> 'S6'."""
    return step.split(":", 1)[0]


def pick_language(preferred: str | None, approved: Iterable[str]) -> str:
    approved = set(approved) or {DEFAULT_LANGUAGE}
    return preferred if preferred in approved else DEFAULT_LANGUAGE


def template_name(step: str, language: str) -> str:
    return f"{TEMPLATES[step_key(step)].base}_{language}"


def build_vars(step: str, *, buyer: str, seller: str, invoice: str, amount: float, when: date) -> List[str]:
    values = {"buyer": buyer.strip()[:60], "seller": seller.strip()[:60], "invoice": invoice.strip()[:40], "amount": inr(amount)[1:], "date": fmt_date(when)}
    return [values[name] for name in TEMPLATES[step_key(step)].vars]
