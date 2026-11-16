"""Reminder engine: turns planner decisions into sent WhatsApp templates, safely.

Safety properties (each has a test):
- Idempotent: one row per (invoice, step) is claimed in the database BEFORE the provider is called. A crash after the
  provider accepted but before we recorded it leaves the row 'sending'/'unknown'; it is never resent automatically.
- Never to a paid, disputed, paused, opted-out or not-opted-in invoice; never outside 09:00-19:00 IST; one message per buyer per day.
- Provider failures: transient -> retry with backoff; permanent -> failed; auth fault -> stop the whole run.
- Circuit breaker after N consecutive failures, a per-run send cap, and a tenant-level pause switch.
- Dry-run returns the decisions without sending anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from .cadence_plan import plan
from .evaluation import evaluate_row
from .messaging import MessageSender, SendResult, normalize_mobile
from .rates import RateTable
from .store import TenantStore, to_rupees
from .templates import DEFAULT_LANGUAGE, TEMPLATES, build_vars, pick_language, step_key, template_name
from .tokens import TokenSigner

IST = timezone(timedelta(hours=5, minutes=30))
RETRY_BACKOFF_MIN = (5, 15, 60, 240)
SENT_STATES = ("sent", "delivered", "read")
QUIET_START, QUIET_END = 9, 19


@dataclass
class RunSummary:
    status: str = "ok"                  # ok | paused | circuit_open | auth_failed | no_sender
    sent: int = 0
    retried: int = 0
    failed: int = 0
    receipts: int = 0
    confirmations: int = 0
    skipped: List[Dict[str, str]] = field(default_factory=list)
    waiting: List[Dict[str, str]] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)       # filled in dry-run


class ReminderEngine:
    def __init__(self, sender: Optional[MessageSender], signer: TokenSigner, *, public_base_url: str, rates: Optional[RateTable] = None,
                 now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc), index_message: Optional[Callable[[str, str], None]] = None,
                 approved_languages=("en",), link_ttl_days: int = 90, max_sends_per_run: int = 200, max_consecutive_failures: int = 3,
                 max_attempts: int = 5):
        self.sender, self.signer, self.rates = sender, signer, rates or RateTable.default()
        self.now_fn, self.index_message = now_fn, index_message
        self.public_base_url = public_base_url.rstrip("/")
        self.approved_languages, self.link_ttl_days = tuple(approved_languages), link_ttl_days
        self.max_sends, self.max_failures, self.max_attempts = max_sends_per_run, max_consecutive_failures, max_attempts

    # ------------------------------------------------------------------ run
    def run(self, store: TenantStore, *, dry_run: bool = False) -> RunSummary:
        out = RunSummary()
        if store.get_setting("reminders_paused") == "1":
            out.status = "paused"
            return out
        if self.sender is None and not dry_run:
            out.status = "no_sender"
            return out
        now = self.now_fn()
        local = now.astimezone(IST).replace(tzinfo=None)
        ctx = _Ctx(store, now, local, out, dry_run, {"fail": 0, "sent": 0})
        supplier = store.get_supplier()
        seller = (supplier or {}).get("business_name") or "your supplier"

        self._retries(ctx, seller)
        if out.status == "ok":
            self._confirmations(ctx, seller)
        if out.status == "ok":
            self._receipts(ctx, seller)
        if out.status == "ok":
            self._cadence(ctx, supplier, seller)
        return out

    # ------------------------------------------------------------------ phases
    def _in_quiet_hours(self, local: datetime) -> bool:
        return not (QUIET_START <= local.hour < QUIET_END)

    def _cadence(self, c: "_Ctx", supplier: Optional[Dict[str, Any]], seller: str) -> None:
        day_start = datetime.combine(c.local.date(), datetime.min.time()).replace(tzinfo=IST).astimezone(timezone.utc).isoformat(timespec="seconds")
        for row in c.store.list_invoices(include_paid=False):
            result = evaluate_row(row, supplier, c.local.date(), self.rates)
            events = c.store.events_for_steps(row["id"])
            decision = plan(
                result, [e["step"] for e in events],
                opted_in=bool(row.get("opt_in_at")) and not row.get("opted_out_at"), now=c.local, paused=bool(row["paused"]),
                disputed=row["status"] == "DISPUTED", promised_date=date.fromisoformat(row["promised_date"]) if row.get("promised_date") else None,
                sent_today_to_buyer=c.store.count_buyer_events_since(row["buyer_id"], day_start),
            )
            label = {"invoice": row["invoice_number"], "reason": decision.reason}
            if decision.action == "none":
                c.out.waiting.append(label) if decision.reason == "NO_STEP_DUE" else None
                continue
            if decision.action == "wait":
                c.out.waiting.append(label)
                continue
            if c.dry_run:
                c.out.decisions.append({"invoice": row["invoice_number"], "step": decision.step, "buyer": row["buyer_name"]})
                continue
            if c.counters["sent"] >= self.max_sends:
                c.out.waiting.append({"invoice": row["invoice_number"], "reason": "RUN_CAP"})
                continue
            when = date.fromisoformat(row["promised_date"]) if decision.step == "P1" and row.get("promised_date") else result.due_date
            self._deliver(c, row, decision.step, seller, amount=result.outstanding, when=when)
            if c.out.status != "ok":
                return

    def _confirmations(self, c: "_Ctx", seller: str) -> None:
        """S6: thank a buyer who promised a date (once per promised date)."""
        for ev in c.store.promise_events():
            if c.out.status != "ok":
                return
            row = c.store.get_invoice(ev["invoice_id"])
            if row is None or row["status"] in ("PAID", "DISPUTED"):
                continue
            step = f"S6:{ev['payload'].get('date')}"
            if any(e["step"] == step for e in c.store.events_for_steps(row["id"])) or not self._consented(row):
                continue
            if self._in_quiet_hours(c.local):
                c.out.waiting.append({"invoice": row["invoice_number"], "reason": "QUIET_HOURS"})
                continue
            if c.dry_run:
                c.out.decisions.append({"invoice": row["invoice_number"], "step": step, "buyer": row["buyer_name"]})
                continue
            if self._deliver(c, row, step, seller, amount=to_rupees(row["outstanding_paise"]), when=date.fromisoformat(ev["payload"]["date"])):
                c.out.confirmations += 1

    def _receipts(self, c: "_Ctx", seller: str) -> None:
        """S7: tell the buyer a payment arrived and reminders have stopped (only if we had reminded them)."""
        for row in c.store.paid_invoices_awaiting_receipt():
            if c.out.status != "ok":
                return
            if not self._consented(row):
                continue
            if self._in_quiet_hours(c.local):
                c.out.waiting.append({"invoice": row["invoice_number"], "reason": "QUIET_HOURS"})
                continue
            if c.dry_run:
                c.out.decisions.append({"invoice": row["invoice_number"], "step": "S7", "buyer": row["buyer_name"]})
                continue
            if self._deliver(c, row, "S7", seller, amount=to_rupees(row["amount_paise"]), when=c.local.date()):
                c.out.receipts += 1

    def _retries(self, c: "_Ctx", seller: str) -> None:
        if c.dry_run or self._in_quiet_hours(c.local):
            return
        for ev in c.store.due_retries(c.now.isoformat(timespec="seconds")):
            row = c.store.get_invoice(ev["invoice_id"])
            if row is None or row["status"] in ("PAID", "DISPUTED") and step_key(ev["step"]) not in ("S6", "S7"):
                c.store.update_event(ev["id"], status="failed", error="invoice no longer open")
                continue
            self._send_existing(c, ev, row, seller)
            if c.out.status != "ok":
                return

    # ------------------------------------------------------------------ delivery
    @staticmethod
    def _consented(row: Dict[str, Any]) -> bool:
        return bool(row.get("opt_in_at")) and not row.get("opted_out_at")

    def _ensure_token(self, store: TenantStore, invoice_id: str, now: datetime) -> str:
        soon = (now + timedelta(days=7)).isoformat(timespec="seconds")
        for l in store.list_links(invoice_id):
            if not l["revoked"] and l["expires_at"] > soon:
                return self.signer.issue(store.tid, l["id"], int(datetime.fromisoformat(l["expires_at"]).timestamp()))
        expires = now + timedelta(days=self.link_ttl_days)
        lid = store.create_link(invoice_id, expires.isoformat(timespec="seconds"))
        return self.signer.issue(store.tid, lid, int(expires.timestamp()))

    def _message_parts(self, c: "_Ctx", row: Dict[str, Any], step: str, seller: str, amount: float, when: date):
        lang = pick_language(row.get("buyer_language"), self.approved_languages)
        tdef = TEMPLATES[step_key(step)]
        body = build_vars(step, buyer=row["buyer_name"], seller=seller, invoice=row["invoice_number"], amount=amount, when=when)
        button = self._ensure_token(c.store, row["id"], c.now) if tdef.button else None
        return template_name(step, lang), lang, body, button

    def _deliver(self, c: "_Ctx", row: Dict[str, Any], step: str, seller: str, *, amount: float, when: date) -> bool:
        mobile = normalize_mobile(row.get("buyer_mobile"))
        if mobile is None:
            c.out.skipped.append({"invoice": row["invoice_number"], "reason": "NO_VALID_MOBILE"})
            return False
        template, lang, body, button = self._message_parts(c, row, step, seller, amount, when)
        eid = c.store.claim_step(row["id"], step, "whatsapp", template, lang, c.now.isoformat(timespec="seconds"))
        if eid is None:
            return False                                         # already claimed: never send twice
        ev = {"id": eid, "step": step, "attempts": 0}
        return self._attempt(c, ev, row, mobile, template, lang, body, button)

    def _send_existing(self, c: "_Ctx", ev: Dict[str, Any], row: Dict[str, Any], seller: str) -> bool:
        mobile = normalize_mobile(row.get("buyer_mobile"))
        if mobile is None or not self._consented(row):
            c.store.update_event(ev["id"], status="failed", error="no valid mobile or consent")
            return False
        step = ev["step"]
        amount = to_rupees(row["amount_paise"] if step_key(step) == "S7" else row["outstanding_paise"])
        when = date.fromisoformat(row["promised_date"]) if step_key(step) == "P1" and row.get("promised_date") else \
            (date.fromisoformat(step.split(":", 1)[1]) if ":" in step else c.local.date())
        if step_key(step) in ("S1", "S2", "S3", "S4", "S5"):
            result = evaluate_row(row, c.store.get_supplier(), c.local.date(), self.rates)
            when = result.due_date
        template, lang, body, button = self._message_parts(c, row, step, seller, amount, when)
        return self._attempt(c, ev, row, mobile, template, lang, body, button)

    def _attempt(self, c: "_Ctx", ev: Dict[str, Any], row: Dict[str, Any], mobile: str, template: str, lang: str, body: List[str], button: Optional[str]) -> bool:
        attempts = int(ev.get("attempts") or 0) + 1
        try:
            res: SendResult = self.sender.send_template(to=mobile, template=template, language=lang, body_vars=body, button_suffix=button)
        except Exception as e:                                   # unknown outcome: never auto-resend
            c.store.update_event(ev["id"], status="unknown", attempts=attempts, error=f"{type(e).__name__}")
            c.out.failed += 1
            return self._count_failure(c)
        if res.ok:
            c.store.update_event(ev["id"], status="sent", attempts=attempts, provider_message_id=res.provider_id, sent_at=c.now.isoformat(timespec="seconds"), error=None, next_attempt_at=None)
            if res.provider_id and self.index_message:
                self.index_message(res.provider_id, c.store.tid)
            c.counters["fail"] = 0
            c.counters["sent"] += 1
            c.out.sent += 1
            return True
        if res.fatal:
            c.store.update_event(ev["id"], status="retry", attempts=attempts, error=res.error, next_attempt_at=(c.now + timedelta(hours=1)).isoformat(timespec="seconds"))
            c.out.status = "auth_failed"
            return False
        if res.retryable and attempts < self.max_attempts:
            delay = RETRY_BACKOFF_MIN[min(attempts - 1, len(RETRY_BACKOFF_MIN) - 1)]
            c.store.update_event(ev["id"], status="retry", attempts=attempts, error=res.error, next_attempt_at=(c.now + timedelta(minutes=delay)).isoformat(timespec="seconds"))
            c.out.retried += 1
        else:
            c.store.update_event(ev["id"], status="failed", attempts=attempts, error=res.error)
            c.out.failed += 1
        return self._count_failure(c)

    def _count_failure(self, c: "_Ctx") -> bool:
        c.counters["fail"] += 1
        if c.counters["fail"] >= self.max_failures and c.out.status == "ok":
            c.out.status = "circuit_open"
        return False


@dataclass
class _Ctx:
    store: TenantStore
    now: datetime
    local: datetime
    out: RunSummary
    dry_run: bool
    counters: Dict[str, int]
