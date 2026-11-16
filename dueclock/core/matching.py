"""Payment matching: which open invoices does a bank credit pay? Rules, not machine learning (v1).

Evidence combined into a 0-1 score:
- the invoice number appears in the narration            (+0.50)
- amount equals the invoice (or a subset of one buyer's invoices): exact +0.35; short by up to the TDS tolerance +0.25
- payer name resembles the buyer (or is a learned alias)  (up to +0.30)
- credit arrives after the invoice date, within 60 days   (+0.05)
Lump sums are solved as a subset-sum over one buyer's open invoices. Every suggestion still needs a person to confirm (D-22).
All amounts are integer paise. Pure functions: no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

DEFAULT_TOLERANCE_PCT = 2.5          # buyers often deduct TDS: accept a small short-payment
MIN_SCORE = 0.40
MAX_SUBSET_INVOICES = 15
MAX_AGE_DAYS = 365

_UTR = re.compile(r"(?<!\d)(\d{12})(?!\d)")
_VPA = re.compile(r"[A-Za-z0-9._-]{2,64}@[A-Za-z][A-Za-z0-9]{1,20}")
_BANKREF = re.compile(r"\b([A-Z]{4}[A-Z0-9]?\d{8,18})\b")
_STOP = {"pvt", "private", "ltd", "limited", "llp", "co", "company", "corp", "corporation", "inc", "the", "and", "of", "ms", "mr", "mrs", "m"}
_CHANNEL_WORDS = {"UPI", "NEFT", "IMPS", "RTGS", "CHQ", "CHEQUE", "CR", "CREDIT", "PAYMENT", "PAY", "TRANSFER", "TRF", "BY", "TO", "FROM",
                  "REF", "UTR", "RRN", "INB", "MOB", "ECS", "NACH", "BANK", "HDFC", "ICIC", "ICICI", "SBIN", "SBI", "AXIS", "UTIB", "KKBK", "YESB", "PUNB"}


def _tokens(s: str) -> List[str]:
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.upper()) if t]


@dataclass(frozen=True)
class ParsedNarration:
    channel: str
    utr: Optional[str]
    vpa: Optional[str]
    name: Optional[str]


def parse_narration(text: Optional[str]) -> ParsedNarration:
    t = text or ""
    up = t.upper()
    channel = next((c for c in ("UPI", "IMPS", "RTGS", "NEFT", "CHQ", "NACH") if c in up), "OTHER")
    m = _UTR.search(t)
    utr = m.group(1) if m else None
    if utr is None:
        b = _BANKREF.search(up)
        utr = b.group(1) if b else None
    vm = _VPA.search(t)
    vpa = vm.group(0).lower() if vm else None
    rest = _VPA.sub(" ", t)
    parts = [p.strip() for p in re.split(r"[/|,\-]", rest)]
    best = None
    for p in parts:
        words = [w for w in re.split(r"\s+", p) if w]
        if not words or any(ch.isdigit() for ch in p) or all(w.upper() in _CHANNEL_WORDS for w in words):
            continue
        cand = " ".join(w for w in words if w.upper() not in _CHANNEL_WORDS)
        if len(cand) >= 3 and (best is None or len(cand) > len(best)):
            best = cand
    return ParsedNarration(channel, utr, vpa, best)


def name_tokens(name: Optional[str]) -> Set[str]:
    return {t for t in (x.lower() for x in re.split(r"[^A-Za-z0-9]+", name or "")) if t and t not in _STOP}


def alias_key(name: Optional[str]) -> str:
    return " ".join(sorted(name_tokens(name)))


def name_similarity(a: Optional[str], b: Optional[str]) -> float:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    contain = 0.9 if (ta <= tb or tb <= ta) and min(len(ta), len(tb)) >= 1 and len(ta & tb) == min(len(ta), len(tb)) else 0.0
    seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(jaccard, contain, seq if seq >= 0.8 else 0.0), 4)


def reference_hit(narration: Optional[str], invoice_number: str) -> bool:
    """The invoice number appears in the narration as a contiguous run of tokens (so INV/1 does not match INV/12)."""
    n, i = _tokens(narration or ""), _tokens(invoice_number)
    if not i or len("".join(i)) < 4:
        return False
    for k in range(len(n) - len(i) + 1):
        if n[k:k + len(i)] == i:
            return True
    joined_n, joined_i = "".join(n), "".join(i)
    return len(joined_i) >= 7 and joined_i in joined_n and not any(
        joined_n[p + len(joined_i):p + len(joined_i) + 1].isdigit() for p in [joined_n.find(joined_i)])


@dataclass
class Suggestion:
    allocations: List[Dict[str, Any]]            # [{"invoice_id", "invoice_number", "paise"}]
    score: float
    reasons: List[str] = field(default_factory=list)
    buyer_id: Optional[str] = None


def _fifo(subset: Sequence[Dict[str, Any]], credit_paise: int) -> List[Dict[str, Any]]:
    out, left = [], credit_paise
    for inv in sorted(subset, key=lambda i: (i["invoice_date"], i["invoice_number"])):
        take = min(left, inv["effective_paise"])
        if take > 0:
            out.append({"invoice_id": inv["id"], "invoice_number": inv["invoice_number"], "paise": take})
            left -= take
    return out


def _subsets_hitting(invoices: Sequence[Dict[str, Any]], target: int, tol_pct: float) -> List[Tuple[Tuple[Dict[str, Any], ...], bool]]:
    """Subsets whose total equals the credit (exact) or exceeds it by at most the tolerance (short payment)."""
    invs = list(invoices)[:MAX_SUBSET_INVOICES]
    hi = int(target / (1 - tol_pct / 100.0)) if tol_pct else target
    found: List[Tuple[Tuple[Dict[str, Any], ...], bool]] = []
    for r in range(1, len(invs) + 1):
        for combo in combinations(invs, r):
            total = sum(i["effective_paise"] for i in combo)
            if total == target:
                found.append((combo, True))
            elif target < total <= hi:
                found.append((combo, False))
        if len(found) >= 40:
            break
    return found


def suggest(credit: Dict[str, Any], invoices: Sequence[Dict[str, Any]], aliases: Optional[Dict[str, str]] = None, *,
            tolerance_pct: float = DEFAULT_TOLERANCE_PCT, max_suggestions: int = 3, min_score: float = MIN_SCORE) -> List[Suggestion]:
    """``credit``: txn_date (ISO), amount_paise, narration, payer_name. ``invoices``: id, invoice_number, invoice_date, buyer_id,
    buyer_name, effective_paise."""
    aliases = aliases or {}
    credit_date = date.fromisoformat(credit["txn_date"])
    amount = int(credit["amount_paise"])
    parsed = parse_narration(credit.get("narration"))
    payer = credit.get("payer_name") or parsed.name
    elig = [i for i in invoices if i["effective_paise"] > 0 and date.fromisoformat(i["invoice_date"]) <= credit_date
            and (credit_date - date.fromisoformat(i["invoice_date"])).days <= MAX_AGE_DAYS]
    if not elig:
        return []

    ref_hits = {i["id"] for i in elig if reference_hit(credit.get("narration"), i["invoice_number"])}
    akey = alias_key(payer)
    alias_buyer = aliases.get(akey) if akey else None
    buyers: Dict[str, List[Dict[str, Any]]] = {}
    for i in elig:
        buyers.setdefault(i["buyer_id"], []).append(i)
    name_score = {b: (1.0 if alias_buyer == b else name_similarity(payer, rows[0]["buyer_name"])) for b, rows in buyers.items()}

    cands: Dict[Tuple[str, ...], Suggestion] = {}

    def add(subset: Sequence[Dict[str, Any]], exact: Optional[bool], reasons: List[str], base: float = 0.0) -> None:
        if exact is None:
            score, tag = base, None
        else:
            score = base + (0.35 if exact else 0.25)
            tag = "AMOUNT_EXACT" if exact else "AMOUNT_WITHIN_TDS_TOLERANCE"
        buyer_ids = {i["buyer_id"] for i in subset}
        b = next(iter(buyer_ids)) if len(buyer_ids) == 1 else None
        if b is not None:
            score += 0.30 * name_score.get(b, 0.0)
            if alias_buyer == b:
                reasons = reasons + ["LEARNED_PAYER"]
            elif name_score.get(b, 0.0) >= 0.6:
                reasons = reasons + ["PAYER_NAME_MATCH"]
        if any(i["id"] in ref_hits for i in subset):
            score += 0.50
            reasons = reasons + ["INVOICE_NUMBER_IN_NARRATION"]
        if tag:
            reasons = reasons + [tag]
        if all(0 <= (credit_date - date.fromisoformat(i["invoice_date"])).days <= 60 for i in subset):
            score += 0.05
        score -= 0.05 * (len(subset) - 1)
        key = tuple(sorted(i["id"] for i in subset))
        s = Suggestion(_fifo(subset, amount), round(max(0.0, min(1.0, score)), 3), reasons, b)
        if key not in cands or cands[key].score < s.score:
            cands[key] = s

    # A. invoices named in the narration (alone or together)
    named = [i for i in elig if i["id"] in ref_hits]
    if named:
        for subset, exact in _subsets_hitting(named, amount, tolerance_pct):
            add(subset, exact, [])
        if not cands:
            add(named[:1], None, ["PARTIAL_OR_ON_ACCOUNT"], base=0.15)
    # B. a buyer we recognise: lump sums over their open invoices
    for b, rows in buyers.items():
        if name_score[b] >= 0.6:
            hits = _subsets_hitting(sorted(rows, key=lambda i: i["invoice_date"]), amount, tolerance_pct)
            for subset, exact in hits:
                add(subset, exact, [])
            if not hits:
                add(sorted(rows, key=lambda i: i["invoice_date"])[:1], None, ["PARTIAL_OR_ON_ACCOUNT"], base=0.1)
    # C. nothing recognisable: a unique exact-amount invoice
    if not cands:
        exact_inv = [i for i in elig if i["effective_paise"] == amount]
        if len(exact_inv) == 1:
            add(exact_inv, True, ["UNIQUE_AMOUNT"])

    out = sorted((s for s in cands.values() if s.score >= min_score), key=lambda s: (-s.score, len(s.allocations)))
    return out[:max_suggestions]
