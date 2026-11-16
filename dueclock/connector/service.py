"""Connector orchestration: probe Tally, read bills, send only changes, flush the queue, log the run.

Change detection: each bill gets a hash; unchanged bills are not re-sent. Tally's outstanding list only holds
open bills, so a settled bill simply disappears. The connector reports vanished bills as settled (pending 0),
but if too many vanish at once (wrong company open, filter problem) it holds them and says so.
"""
from __future__ import annotations

import hashlib
import json
import logging
import signal
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from dueclock.core.ingest import SyncRecord, invoice_key

from .config import ConnectorConfig
from .local_store import LocalStore, utcnow
from .tally_client import TallyClient, TallyError
from .uploader import AuthRejected, Uploader, build_payload

log = logging.getLogger("dueclock.connector")


@dataclass
class RunResult:
    status: str                              # ok | tally_unreachable | tally_error | parse_failed | held | auth_rejected | partial
    tally_product: str = "unknown"
    companies: List[str] = field(default_factory=list)
    fetched: int = 0
    changed: int = 0
    queued: int = 0
    settled_reported: int = 0
    held_vanished: int = 0
    parse_errors: int = 0
    sent_records: int = 0
    retried: int = 0
    dead: int = 0
    queue_pending: int = 0
    notes: List[str] = field(default_factory=list)


def record_hash(rec: Dict[str, Any]) -> str:
    keep = {k: rec.get(k) for k in ("invoice_number", "invoice_date", "buyer_name", "amount", "pending_amount", "agreed_credit_days", "buyer_gstin", "buyer_mobile")}
    return hashlib.sha1(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()


def _key(rec: Dict[str, Any]) -> Optional[str]:
    try:
        return invoice_key(SyncRecord.model_validate(rec))
    except Exception:
        return None


def _read_template(path: str) -> Optional[str]:
    if not path:
        return None
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


class ConnectorService:
    def __init__(self, cfg: ConnectorConfig, store: Optional[LocalStore] = None, tally: Optional[TallyClient] = None, uploader: Optional[Uploader] = None):
        self.cfg = cfg
        self.store = store or LocalStore(cfg.state_path)
        self.tally = tally or TallyClient(cfg.tally_host, cfg.tally_port, cfg.tally_timeout_s, profile=cfg.tally_profile, bills_template=_read_template(cfg.tally_bills_template_path))
        self.uploader = uploader or Uploader(cfg, self.store)
        self.stop_event = threading.Event()

    # ---- one run
    def run_once(self, *, dry_run: bool = False, confirm_vanished: bool = False, now: Optional[datetime] = None) -> RunResult:
        started = now or utcnow()
        res = RunResult(status="ok")
        probe = self.tally.probe()
        res.tally_product = probe.product
        if not probe.running:
            res.status = "tally_unreachable"
            res.notes.append(probe.message)
        else:
            self._collect(res, dry_run, confirm_vanished, started)
        if not dry_run:
            try:
                up = self.uploader.flush(started)
                res.sent_records, res.retried, res.dead = up.records_sent, up.retried, up.dead
                res.notes += up.notes
            except AuthRejected:
                res.status = "auth_rejected"
                res.notes.append("the cloud rejected the API key; uploads stopped")
            res.queue_pending = self.store.queue_stats()["pending"]
            self.store.purge_sent(7, started)
            self.store.record_run(started, utcnow(), res.status, asdict(res))
        log.info("run %s: fetched=%d queued=%d sent=%d held=%d retried=%d dead=%d",
                 res.status, res.fetched, res.queued, res.sent_records, res.held_vanished, res.retried, res.dead)
        return res

    def _companies(self) -> List[Optional[str]]:
        return list(self.cfg.companies) or [None]

    def _collect(self, res: RunResult, dry_run: bool, confirm_vanished: bool, now: datetime) -> None:
        for company in self._companies():
            label = company or "(open company)"
            res.companies.append(label)
            try:
                parsed = self.tally.fetch_bills(company)
            except TallyError as e:
                res.status = "tally_error"
                res.notes.append(f"{label}: {e}")
                continue
            res.parse_errors += len(parsed.errors)
            if not parsed.records and parsed.errors:
                res.status = "parse_failed" if res.status == "ok" else res.status
                res.notes.append(f"{label}: nothing parsed ({parsed.errors[0]})")
                continue
            res.fetched += len(parsed.records)
            self._diff_and_queue(label, parsed.records, res, dry_run, confirm_vanished, now)

    def _diff_and_queue(self, label: str, records: List[Dict[str, Any]], res: RunResult, dry_run: bool, confirm_vanished: bool, now: datetime) -> None:
        snap = self.store.snapshot(label)
        current: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            k = rec.get("guid") or _key(rec)
            if k:
                current[k] = rec
        changed = {k: r for k, r in current.items() if k not in snap or snap[k]["hash"] != record_hash(r)}

        prev_open = {k for k, e in snap.items() if (e["pending"] or 0) > 0}
        vanished = prev_open - set(current)
        settle: Dict[str, Dict[str, Any]] = {}
        if vanished:
            suspicious = len(vanished) >= self.cfg.mass_settlement_min and len(vanished) / max(1, len(prev_open)) > self.cfg.mass_settlement_ratio
            if suspicious and not confirm_vanished:
                res.held_vanished += len(vanished)
                if res.status == "ok":
                    res.status = "held"
                res.notes.append(f"{label}: {len(vanished)} of {len(prev_open)} open bills vanished at once; held (check the company open in Tally, then run with --confirm-vanished)")
            else:
                for k in vanished:
                    base = dict(snap[k]["record"])
                    base["pending_amount"] = 0.0
                    settle[k] = base
        res.changed += len(changed)
        res.settled_reported += len(settle)
        outbound = list(changed.values()) + list(settle.values())
        if dry_run or not outbound:
            return
        entries: Dict[str, Dict[str, Any]] = {}
        for k, r in changed.items():
            entries[k] = {"record": r, "hash": record_hash(r), "pending": r.get("pending_amount", 0)}
        for k, r in settle.items():
            entries[k] = {"record": r, "hash": record_hash(r), "pending": 0.0}
        for i in range(0, len(outbound), self.cfg.batch_size):
            self.store.enqueue(build_payload(outbound[i:i + self.cfg.batch_size], res.tally_product), now)
            res.queued += min(self.cfg.batch_size, len(outbound) - i)
        self.store.save_snapshot(label, entries)

    # ---- scheduler
    def next_run_after(self, now: datetime) -> datetime:
        target = now.replace(hour=self.cfg.schedule_hour, minute=self.cfg.schedule_minute, second=0, microsecond=0)
        return target if target > now else target + timedelta(days=1)

    def serve_forever(self, poll_s: float = 30.0) -> None:
        """Run nightly at the configured time, retry the queue every few minutes, stop cleanly on SIGINT/SIGTERM."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: self.stop_event.set())
            except ValueError:
                pass                                       # not in the main thread
        next_run = utcnow()
        log.info("connector started; next sync %s", next_run.isoformat())
        while not self.stop_event.is_set():
            now = utcnow()
            if now >= next_run:
                try:
                    self.run_once()
                except Exception:
                    log.exception("run failed")
                next_run = self.next_run_after(utcnow())
            else:
                try:
                    self.uploader.flush(now)
                except AuthRejected:
                    log.error("API key rejected")
                except Exception:
                    log.exception("flush failed")
            self.stop_event.wait(poll_s)
        log.info("connector stopped")

    # ---- diagnostics
    def diagnose(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"connector_version": __import__("dueclock.connector", fromlist=["x"]).CONNECTOR_VERSION}
        p = self.tally.probe()
        out["tally"] = {"running": p.running, "product": p.product, "message": p.message}
        if p.running:
            try:
                out["tally"]["companies"] = self.tally.list_companies()
            except TallyError as e:
                out["tally"]["companies_error"] = str(e)
        try:
            out["cloud"] = {"ok": True, **self.uploader.ping()}
        except AuthRejected:
            out["cloud"] = {"ok": False, "error": "API key rejected"}
        except Exception as e:
            out["cloud"] = {"ok": False, "error": type(e).__name__}
        out["queue"] = self.store.queue_stats()
        out["last_runs"] = [{"at": r["finished_at"], "status": r["status"]} for r in self.store.last_runs(3)]
        return out


class RedactKey(logging.Filter):
    def __init__(self, secret: str):
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secret:
            record.msg = str(record.msg).replace(self.secret, "[key]")
            if record.args:
                record.args = tuple(str(a).replace(self.secret, "[key]") if isinstance(a, str) else a for a in record.args)
        return True


def setup_logging(cfg: ConnectorConfig) -> None:
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    if cfg.log_path:
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(cfg.log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        h.addFilter(RedactKey(cfg.api_key))
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)      # no request URLs in the log
