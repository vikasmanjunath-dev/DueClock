"""Uploads queued batches to the cloud core with the right reaction to every failure.

- 2xx: sent.            - 401/403: stop, do not retry (the key is wrong or revoked): raises AuthRejected.
- 413: split the batch. - 422 / other 4xx: the payload is bad: dead-letter, do not retry.
- 429, 5xx, network errors: retry with backoff (honouring Retry-After).
"""
from __future__ import annotations

import platform
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from . import CONNECTOR_VERSION
from .config import ConnectorConfig
from .local_store import LocalStore, utcnow


class AuthRejected(Exception):
    pass


@dataclass
class UploadSummary:
    sent: int = 0
    records_sent: int = 0
    retried: int = 0
    dead: int = 0
    split: int = 0
    record_errors: int = 0
    notes: List[str] = field(default_factory=list)


def build_payload(records: List[Dict[str, Any]], tally_version: Optional[str]) -> Dict[str, Any]:
    return {"connector_version": CONNECTOR_VERSION, "tally_version": tally_version, "os": f"{platform.system()} {platform.release()}", "records": records}


class Uploader:
    def __init__(self, cfg: ConnectorConfig, store: LocalStore, client: Optional[httpx.Client] = None):
        self.cfg, self.store = cfg, store
        self.client = client or httpx.Client(timeout=cfg.cloud_timeout_s)
        self.headers = {"X-API-Key": cfg.api_key, "User-Agent": f"dueclock-connector/{CONNECTOR_VERSION}"}
        self.base = cfg.cloud_url.rstrip("/")

    def ping(self) -> Dict[str, Any]:
        r = self.client.get(f"{self.base}/v1/connector/ping", headers=self.headers)
        if r.status_code in (401, 403):
            raise AuthRejected("the cloud rejected the API key")
        r.raise_for_status()
        return r.json()

    def flush(self, now: Optional[datetime] = None) -> UploadSummary:
        now = now or utcnow()
        out = UploadSummary()
        for b in self.store.due_batches(now):
            payload = b["payload"]
            try:
                r = self.client.post(f"{self.base}/v1/connector/sync", json=payload, headers=self.headers)
            except httpx.HTTPError as e:
                self._fail(b["id"], f"network: {type(e).__name__}", out, now)
                continue
            code = r.status_code
            if code == 200:
                self.store.mark_sent(b["id"])
                out.sent += 1
                out.records_sent += b["records"]
                try:
                    out.record_errors += len(r.json().get("errors", []))
                except ValueError:
                    pass
            elif code in (401, 403):
                raise AuthRejected("the cloud rejected the API key")
            elif code == 413 and len(payload["records"]) > 1:
                half = len(payload["records"]) // 2
                parts = [dict(payload, records=payload["records"][:half]), dict(payload, records=payload["records"][half:])]
                self.store.replace_batch(b["id"], parts, now)
                out.split += 1
            elif code == 429 or code >= 500:
                ra = r.headers.get("Retry-After")
                self._fail(b["id"], f"HTTP {code}", out, now, float(ra) if ra and ra.isdigit() else None)
            else:
                self.store.mark_dead(b["id"], f"HTTP {code}: {r.text[:120]}")
                out.dead += 1
                out.notes.append(f"batch dead-lettered after HTTP {code}")
        return out

    def _fail(self, batch_id: str, error: str, out: UploadSummary, now: datetime, retry_after: Optional[float] = None) -> None:
        state = self.store.mark_failed(batch_id, error, self.cfg.max_attempts, now, retry_after)
        if state == "dead":
            out.dead += 1
            out.notes.append(f"batch dead after {self.cfg.max_attempts} attempts: {error}")
        else:
            out.retried += 1
