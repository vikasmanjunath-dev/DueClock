"""Dated RBI bank-rate table.

MSMED Act section 16 interest is a multiple (3x) of the bank rate notified by the RBI. The rate
changes over time, so it is a table of effective-from dates, never a constant.

The default table is the RBI Bank Rate history (Bank Rate = MSF rate = repo + 0.25 since 2016), each row effective
immediately on the policy date. Rows are ``confirmed=True`` only where read from an RBI circular/table; the rest come from
consistent secondary sources and stay unconfirmed (flag RATE_TABLE_UNCONFIRMED) until checked against RBI's Table 46.
``verified_through`` is the last date the table was checked for new RBI changes; past it the engine raises RATE_TABLE_STALE.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class RateEntry:
    effective_from: date
    bank_rate_pct: float
    confirmed: bool = False
    source: str = ""


_RBI = "RBI policy-rate history (secondary sources; check against RBI Table 46)"
_RBI_CIRCULAR = "RBI circular/table (Bank Rate, MPS 2020-21)"
# (effective date, Bank Rate %, confirmed against an RBI document, source)
BANK_RATE_HISTORY = (
    (date(2017, 8, 2), 6.25, False, _RBI), (date(2018, 6, 6), 6.50, False, _RBI), (date(2018, 8, 1), 6.75, False, _RBI),
    (date(2019, 2, 7), 6.50, False, _RBI), (date(2019, 4, 4), 6.25, False, _RBI), (date(2019, 6, 6), 6.00, False, _RBI),
    (date(2019, 8, 7), 5.65, False, _RBI), (date(2019, 10, 4), 5.40, False, _RBI),
    (date(2020, 3, 27), 4.65, True, _RBI_CIRCULAR), (date(2020, 5, 22), 4.25, True, _RBI_CIRCULAR),
    (date(2022, 5, 4), 4.65, False, _RBI), (date(2022, 6, 8), 5.15, False, _RBI), (date(2022, 8, 5), 5.65, False, _RBI),
    (date(2022, 9, 30), 6.15, False, _RBI), (date(2022, 12, 7), 6.50, False, _RBI), (date(2023, 2, 8), 6.75, False, _RBI),
    (date(2025, 2, 7), 6.50, False, _RBI), (date(2025, 4, 9), 6.25, False, _RBI), (date(2025, 6, 6), 5.75, False, _RBI),
    (date(2025, 12, 5), 5.50, False, _RBI), (date(2026, 10, 7), 5.75, False, _RBI),
)
HISTORY_VERIFIED_THROUGH = date(2026, 11, 10)
STALE_AFTER_DAYS = 60


class RateTable:
    def __init__(self, entries: Iterable[RateEntry], verified_through: Optional[date] = None):
        self.entries: List[RateEntry] = sorted(entries, key=lambda e: e.effective_from)
        if not self.entries:
            raise ValueError("rate table needs at least one entry")
        self.verified_through = verified_through

    def is_stale(self, as_of: date) -> bool:
        """True when ``as_of`` is well past the last check, so a later RBI change could be missing."""
        return self.verified_through is not None and as_of > self.verified_through + timedelta(days=STALE_AFTER_DAYS)

    def rate_on(self, d: date) -> float:
        """Bank rate in force on ``d``. Before the first entry, the first entry applies."""
        current = self.entries[0].bank_rate_pct
        for e in self.entries:
            if e.effective_from <= d:
                current = e.bank_rate_pct
            else:
                break
        return current

    @property
    def fully_confirmed(self) -> bool:
        return all(e.confirmed for e in self.entries)

    @classmethod
    def default(cls) -> "RateTable":
        return cls([RateEntry(d, r, c, src) for d, r, c, src in BANK_RATE_HISTORY], HISTORY_VERIFIED_THROUGH)

    @classmethod
    def from_json(cls, text: str) -> "RateTable":
        rows = json.loads(text)
        return cls(
            RateEntry(date.fromisoformat(r["effective_from"]), float(r["bank_rate_pct"]), bool(r.get("confirmed", False)), r.get("source", ""))
            for r in rows
        )
