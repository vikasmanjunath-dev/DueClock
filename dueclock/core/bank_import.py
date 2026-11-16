"""Bank statement import: CSV text or JSON rows -> credit records ready for matching. Debits are ignored; duplicates are skipped."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .matching import parse_narration
from .store import to_paise
from .tally_parse import parse_amount, parse_tally_date

DATE_COLS = ("date", "txn date", "transaction date", "value date", "tran date", "posting date")
NARR_COLS = ("narration", "description", "particulars", "remarks", "transaction remarks", "details")
CREDIT_COLS = ("credit", "credit amount", "deposit", "deposits", "deposit amt", "deposit amount", "cr", "cr amount", "amount credited")
DEBIT_COLS = ("debit", "debit amount", "withdrawal", "withdrawals", "withdrawal amt", "dr", "dr amount")
REF_COLS = ("utr", "reference", "ref no", "ref", "chq/ref no", "cheque/ref no", "reference no", "chq./ref.no.")
AMOUNT_COLS = ("amount",)
EXTRA_DATE_FORMATS = ("%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %b %y", "%d.%m.%Y")


@dataclass
class ImportResult:
    credits: List[Dict[str, Any]] = field(default_factory=list)
    ignored_debits: int = 0
    errors: List[str] = field(default_factory=list)


def _date(text: Optional[str]) -> Optional[date]:
    d = parse_tally_date(text)
    if d:
        return d
    for fmt in EXTRA_DATE_FORMATS:
        try:
            return datetime.strptime((text or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def dedupe_key(d: date, amount_paise: int, narration: str, utr: Optional[str], nth: int) -> str:
    base = f"{d.isoformat()}|{amount_paise}|{_norm(narration)}|{utr or ''}"
    return hashlib.sha1(base.encode()).hexdigest()[:32] + f"#{nth}"


def _pick(row: Dict[str, str], names) -> Optional[str]:
    for n in names:
        if n in row and str(row[n]).strip() != "":
            return str(row[n]).strip()
    return None


def parse_rows(rows: List[Dict[str, Any]], source: str = "import") -> ImportResult:
    out = ImportResult()
    seen: Dict[str, int] = {}
    for i, raw in enumerate(rows, start=1):
        row = {_norm(str(k)): v for k, v in raw.items()}
        d = _date(_pick(row, DATE_COLS))
        narration = _pick(row, NARR_COLS) or ""
        credit = parse_amount(_pick(row, CREDIT_COLS)) if _pick(row, CREDIT_COLS) else None
        debit = parse_amount(_pick(row, DEBIT_COLS)) if _pick(row, DEBIT_COLS) else None
        if credit is None and _pick(row, AMOUNT_COLS) and not debit:
            credit = parse_amount(_pick(row, AMOUNT_COLS))
        if d is None:
            if any(str(v).strip() for v in row.values()):
                out.errors.append(f"row {i}: date not understood")
            continue
        if not credit:
            if debit:
                out.ignored_debits += 1
            else:
                out.errors.append(f"row {i}: no credit amount")
            continue
        parsed = parse_narration(narration)
        utr = (_pick(row, REF_COLS) or parsed.utr or "").strip() or None
        paise = to_paise(credit)
        base = dedupe_key(d, paise, narration, utr, 0)[:-2]
        seen[base] = seen.get(base, 0) + 1
        out.credits.append({"txn_date": d.isoformat(), "amount_paise": paise, "utr": utr, "narration": narration[:300], "payer_name": parsed.name,
                            "payer_vpa": parsed.vpa, "source": source, "dedupe_key": f"{base}#{seen[base]}"})
    return out


def parse_csv(text: str, source: str = "csv") -> ImportResult:
    """Finds the header row (banks add preamble lines) and reads the rows below it."""
    lines = [ln for ln in text.splitlines()]
    start = next((i for i, ln in enumerate(lines) if any(c in _norm(ln) for c in ("date",)) and any(c in _norm(ln) for c in NARR_COLS)), None)
    if start is None:
        r = ImportResult()
        r.errors.append("no header row with a date and a narration column was found")
        return r
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    return parse_rows([dict(r) for r in reader], source)
