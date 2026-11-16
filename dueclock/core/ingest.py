"""Idempotent connector sync: validate each record, upsert buyers and invoices, log the run.

A bad record never fails the whole sync; it is counted and reported. Running the same payload twice
changes nothing. The seller-controlled 'written agreement' flag lives on the buyer and is never
overwritten by Tally data.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from .store import TenantStore, now_iso

MAX_RECORDS = 5000


class SyncRecord(BaseModel):
    guid: Optional[str] = None
    invoice_number: str = Field(min_length=1)
    invoice_date: date
    delivery_date: Optional[date] = None
    buyer_name: str = Field(min_length=1)
    buyer_gstin: Optional[str] = None
    buyer_mobile: Optional[str] = None
    amount: float = Field(gt=0)
    pending_amount: float = Field(ge=0)
    agreed_credit_days: int = Field(default=0, ge=0, le=3650)
    amount_known: bool = True      # False when the source gives only the pending balance


def invoice_key(rec: SyncRecord) -> str:
    if rec.guid:
        return rec.guid.strip()
    raw = f"{rec.invoice_number.strip().lower()}|{' '.join(rec.buyer_name.lower().split())}|{rec.invoice_date.isoformat()}"
    return "h_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def ingest_sync(store: TenantStore, payload: Dict[str, Any]) -> Dict[str, Any]:
    started = now_iso()
    raw_records: List[Dict[str, Any]] = payload.get("records") or []
    if len(raw_records) > MAX_RECORDS:
        raise ValueError(f"too many records in one sync (max {MAX_RECORDS})")
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    errors: List[Dict[str, Any]] = []
    with store.db.transaction():
        for idx, raw in enumerate(raw_records):
            try:
                rec = SyncRecord.model_validate(raw)
            except ValidationError as e:
                errors.append({"index": idx, "invoice_number": (raw or {}).get("invoice_number"), "error": e.errors()[0]["msg"]})
                continue
            buyer_id = store.upsert_buyer(rec.buyer_name, rec.buyer_gstin, rec.buyer_mobile)
            outcome, _ = store.upsert_invoice({
                "tally_guid": invoice_key(rec), "invoice_number": rec.invoice_number.strip(), "invoice_date": rec.invoice_date.isoformat(),
                "delivery_date": rec.delivery_date.isoformat() if rec.delivery_date else None, "buyer_id": buyer_id,
                "amount": rec.amount, "pending_amount": rec.pending_amount, "agreed_credit_days": rec.agreed_credit_days,
                "amount_known": rec.amount_known,
            })
            counts[outcome] += 1
        status = "ok" if not errors else ("partial" if sum(counts.values()) else "failed")
        sync_id = store.record_sync(
            connector_version=payload.get("connector_version"), tally_version=payload.get("tally_version"), os=payload.get("os"),
            started_at=started, status=status, received=len(raw_records), errors=len(errors), error_detail=str(errors[:20]) if errors else None,
            **counts,
        )
    return {"sync_id": sync_id, "status": status, "received": len(raw_records), **counts, "errors": errors}
