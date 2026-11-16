"""Reconciliation service: import credits, suggest matches, confirm / reject / undo / assign by hand.

A confirmed allocation adds to the invoice's ``unposted_paise``: money we know arrived that Tally does not show yet.
Effective outstanding = Tally outstanding - unposted, so reminders, the buyer page and the clock stop immediately.
When Tally later records the payment, the next sync reduces ``unposted`` by the same amount (see TenantStore.upsert_invoice).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .bank_import import ImportResult
from .matching import DEFAULT_TOLERANCE_PCT, alias_key, suggest
from .store import TenantStore, now_iso


class ReconError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _refresh_credit(store: TenantStore, credit_id: str) -> None:
    credit = store.get_credit(credit_id)
    if credit is None or credit["status"] == "ignored":
        return
    matched = sum(a["paise"] for m in store.matches_for_credit(credit_id) if m["status"] == "confirmed" for a in m["allocations"])
    has_suggestion = any(m["status"] == "suggested" for m in store.matches_for_credit(credit_id))
    status = "matched" if matched >= credit["amount_paise"] else ("partial" if matched > 0 else ("suggested" if has_suggestion else "unmatched"))
    store.update_credit(credit_id, matched_paise=matched, status=status)


def suggest_for_credit(store: TenantStore, credit_id: str, *, auto_confirm_at: Optional[float] = None, tolerance_pct: float = DEFAULT_TOLERANCE_PCT) -> List[Dict[str, Any]]:
    credit = store.get_credit(credit_id)
    if credit is None or credit["status"] in ("matched", "ignored"):
        return []
    store.delete_suggested_matches(credit_id)
    sugg = suggest(credit, store.matching_invoices(), store.aliases(), tolerance_pct=tolerance_pct)
    ids = []
    for s in sugg:
        ids.append(store.add_match(credit_id, "suggested", s.score, "rules", s.reasons, s.allocations))
    _refresh_credit(store, credit_id)
    if auto_confirm_at is not None and sugg and sugg[0].score >= auto_confirm_at and len(sugg) == 1:
        confirm(store, ids[0], actor="auto")
    return [store.get_match(i) for i in ids]


def import_credits(store: TenantStore, parsed: ImportResult, *, auto_confirm_at: Optional[float] = None) -> Dict[str, Any]:
    new_ids, dup = [], 0
    with store.db.transaction():
        for c in parsed.credits:
            cid = store.insert_credit(c)
            if cid is None:
                dup += 1
            else:
                new_ids.append(cid)
    suggested = 0
    for cid in new_ids:
        if suggest_for_credit(store, cid, auto_confirm_at=auto_confirm_at):
            suggested += 1
    store.audit("seller", "credits.import", f"{len(new_ids)} new, {dup} duplicates")
    return {"imported": len(new_ids), "duplicates": dup, "ignored_debits": parsed.ignored_debits, "errors": parsed.errors,
            "with_suggestions": suggested, "credit_ids": new_ids}


def _check_allocations(store: TenantStore, credit: Dict[str, Any], allocations: List[Dict[str, Any]], already: int) -> None:
    total = sum(a["paise"] for a in allocations)
    if total <= 0 or any(a["paise"] <= 0 for a in allocations):
        raise ReconError("allocations must be positive", 422)
    if already + total > credit["amount_paise"]:
        raise ReconError("allocations exceed the credit amount")
    for a in allocations:
        inv = store.get_invoice(a["invoice_id"])
        if inv is None:
            raise ReconError("invoice not found", 404)
        if a["paise"] > inv["outstanding_paise"] - int(inv.get("unposted_paise") or 0):
            raise ReconError(f"allocation exceeds what is outstanding on {inv['invoice_number']}")


def _apply(store: TenantStore, allocations: List[Dict[str, Any]], sign: int) -> None:
    for a in allocations:
        store.apply_unposted(a["invoice_id"], sign * a["paise"])


def confirm(store: TenantStore, match_id: str, actor: str, at: Optional[str] = None) -> Dict[str, Any]:
    m = store.get_match(match_id)
    if m is None:
        raise ReconError("match not found", 404)
    if m["status"] != "suggested":
        raise ReconError(f"match is already {m['status']}")
    credit = store.get_credit(m["credit_id"])
    with store.db.transaction():
        _check_allocations(store, credit, m["allocations"], credit["matched_paise"])
        _apply(store, m["allocations"], +1)
        store.update_match(match_id, status="confirmed", confirmed_by=actor, confirmed_at=at or now_iso())
        for other in store.matches_for_credit(credit["id"]):
            if other["id"] != match_id and other["status"] == "suggested":
                store.update_match(other["id"], status="rejected")
        _refresh_credit(store, credit["id"])
        _learn_alias(store, credit, m["allocations"])
        store.audit(actor, "match.confirm", match_id)
    return store.get_match(match_id)


def _learn_alias(store: TenantStore, credit: Dict[str, Any], allocations: List[Dict[str, Any]]) -> None:
    key = alias_key(credit.get("payer_name"))
    buyers = {store.get_invoice(a["invoice_id"])["buyer_id"] for a in allocations}
    if key and len(buyers) == 1:
        store.put_alias(key, next(iter(buyers)))


def reject(store: TenantStore, match_id: str, actor: str) -> Dict[str, Any]:
    m = store.get_match(match_id)
    if m is None:
        raise ReconError("match not found", 404)
    if m["status"] != "suggested":
        raise ReconError(f"match is already {m['status']}")
    store.update_match(match_id, status="rejected")
    _refresh_credit(store, m["credit_id"])
    store.audit(actor, "match.reject", match_id)
    return store.get_match(match_id)


def unconfirm(store: TenantStore, match_id: str, actor: str) -> Dict[str, Any]:
    m = store.get_match(match_id)
    if m is None:
        raise ReconError("match not found", 404)
    if m["status"] != "confirmed":
        raise ReconError("only a confirmed match can be undone")
    with store.db.transaction():
        _apply(store, m["allocations"], -1)
        store.update_match(match_id, status="reversed")
        _refresh_credit(store, m["credit_id"])
        store.audit(actor, "match.unconfirm", match_id)
    return store.get_match(match_id)


def assign_manually(store: TenantStore, credit_id: str, allocations: List[Dict[str, Any]], actor: str, at: Optional[str] = None) -> Dict[str, Any]:
    credit = store.get_credit(credit_id)
    if credit is None:
        raise ReconError("credit not found", 404)
    if credit["status"] == "ignored":
        raise ReconError("credit is ignored")
    with store.db.transaction():
        _check_allocations(store, credit, allocations, credit["matched_paise"])
        _apply(store, allocations, +1)
        mid = store.add_match(credit_id, "confirmed", 1.0, "manual", ["MANUAL"], allocations, confirmed_by=actor, at=at)
        _refresh_credit(store, credit_id)
        _learn_alias(store, credit, allocations)
        store.audit(actor, "match.manual", mid)
    return store.get_match(mid)


def ignore_credit(store: TenantStore, credit_id: str, actor: str) -> None:
    credit = store.get_credit(credit_id)
    if credit is None:
        raise ReconError("credit not found", 404)
    if credit["matched_paise"] > 0:
        raise ReconError("undo the confirmed matches first")
    store.delete_suggested_matches(credit_id)
    store.update_credit(credit_id, status="ignored")
    store.audit(actor, "credit.ignore", credit_id)
