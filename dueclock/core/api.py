"""FastAPI router for the core product (/v1). All routes are tenant-scoped by API key (header X-API-Key)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .i18n import norm_lang, t as _tr
from .buyer_page import DISPUTE_REASONS, MAX_PAGE_BYTES, PROMISE_MAX_DAYS, PageData, render_invoice_page, render_notice_page, security_headers
from .clock import financial_year, financial_year_end
from .evaluation import evaluate_row
from . import reconcile
from .bank_import import parse_csv, parse_rows
from .ingest import ingest_sync, MAX_RECORDS
from .messaging import MessageSender, sender_from_env
from .rates import RateTable
from .reminders import ReminderEngine
from .store import ControlPlane, TenantContext, TenantStore, to_paise, to_rupees
from .owner_ui import UiContext, mount_owner_ui
from .scan_ui import mount_scan
from .tokens import SessionSigner, TokenSigner, load_secret


MIN_CONNECTOR_VERSION = "0.1.0"


class SupplierIn(BaseModel):
    business_name: str
    gstin: Optional[str] = None
    udyam_number: Optional[str] = None
    category: str = "UNKNOWN"
    activity: str = "UNKNOWN"
    udyam_registration_date: Optional[date] = None
    upi_vpa: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


class AgreementIn(BaseModel):
    has_written_agreement: bool
    agreed_days: Optional[int] = None


class DisputeResolveIn(BaseModel):
    resolved: bool = True


class ImportIn(BaseModel):
    format: str = "csv"                      # csv | rows
    text: Optional[str] = None
    rows: Optional[List[Dict[str, Any]]] = None


class AllocationIn(BaseModel):
    invoice_id: str
    amount: float


class ManualIn(BaseModel):
    allocations: List[AllocationIn]


class OptInIn(BaseModel):
    source: str


class LanguageIn(BaseModel):
    language: str


class SyncIn(BaseModel):
    connector_version: str
    tally_version: Optional[str] = None
    os: Optional[str] = None
    records: List[Dict[str, Any]]


def _d(text: Optional[str]) -> Optional[date]:
    return date.fromisoformat(text) if text else None


def invoice_view(row: Dict[str, Any], supplier: Optional[Dict[str, Any]], as_of: date, rates: RateTable) -> Dict[str, Any]:
    result = evaluate_row(row, supplier, as_of, rates)
    unposted = int(row.get("unposted_paise") or 0)
    effective = max(0, row["outstanding_paise"] - unposted)
    return {
        "id": row["id"], "invoice_number": row["invoice_number"], "invoice_date": row["invoice_date"], "buyer_id": row["buyer_id"],
        "buyer_name": row["buyer_name"], "amount": to_rupees(row["amount_paise"]), "outstanding": to_rupees(effective), "tally_outstanding": to_rupees(row["outstanding_paise"]), "unposted_payments": to_rupees(unposted),
        "status": row["status"], "written_agreement": bool(row["buyer_agreement"]), "clock": result.to_dict(),
    }


def create_core_app(
    data_dir: Optional[str] = None,
    control_db_url: Optional[str] = None,
    today_fn: Callable[[], date] = date.today,
    rates: Optional[RateTable] = None,
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    public_base_url: Optional[str] = None,
    scan_url: str = "/exposure-scan",
    privacy_url: str = "/privacy",
    grievance_email: str = "support@dueclock.example",
    link_ttl_days: int = 90,
    show_interest: bool = False,
    token_secret: Optional[bytes] = None,
    sender: Optional[MessageSender] = None,
    wa_app_secret: Optional[str] = None,
    wa_verify_token: Optional[str] = None,
    approved_languages=("en",),
    max_sends_per_run: int = 200,
    auto_confirm_at: Optional[float] = None,
) -> FastAPI:
    data_dir = data_dir or os.environ.get("DUECLOCK_DATA_DIR", "./data")
    public_base_url = (public_base_url or os.environ.get("DUECLOCK_PUBLIC_URL", "http://127.0.0.1:8100")).rstrip("/")
    signer = TokenSigner(token_secret or load_secret(data_dir))
    wa_app_secret = wa_app_secret or os.environ.get("DUECLOCK_WA_APP_SECRET")
    wa_verify_token = wa_verify_token or os.environ.get("DUECLOCK_WA_VERIFY_TOKEN")
    rates = rates or RateTable.default()
    control = ControlPlane(data_dir, control_db_url)
    app = FastAPI(title="DueClock core", version="1.0.0")
    app.state.control = control
    engine = ReminderEngine(sender if sender is not None else sender_from_env(), signer, public_base_url=public_base_url, rates=rates, now_fn=now_fn,
                            index_message=control.index_message, approved_languages=approved_languages, link_ttl_days=link_ttl_days,
                            max_sends_per_run=max_sends_per_run)
    app.state.engine = engine

    def tenant(x_api_key: Optional[str] = Header(default=None)) -> TenantStore:
        ctx: Optional[TenantContext] = control.authenticate(x_api_key)
        if ctx is None:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        return control.store_for(ctx)

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(scan_url, status_code=307)      # public front door: the no-login exposure scan

    @app.get("/v1/health")
    def health() -> Dict[str, Any]:
        return {"status": "ok", "rate_table_confirmed": rates.fully_confirmed}

    @app.get("/v1/cron/reminders")
    def cron_reminders(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        """Serverless hosts (Vercel) call this on a schedule instead of running the in-process scheduler. Protected by DUECLOCK_CRON_SECRET
        (Vercel sends it as `Authorization: Bearer <CRON_SECRET>`). Safe to call twice: each reminder step is claimed by a UNIQUE row."""
        secret = os.environ.get("DUECLOCK_CRON_SECRET") or os.environ.get("CRON_SECRET")
        if not secret or not hmac.compare_digest((authorization or "").removeprefix("Bearer ").strip(), secret):
            raise HTTPException(status_code=401, detail="cron secret missing or wrong")
        from .scheduler import ReminderScheduler
        return ReminderScheduler(control, engine).run_once()

    @app.get("/v1/ready")
    def ready() -> Any:
        """Readiness for load balancers: is the control database reachable? (/v1/health only says the process is up.)"""
        try:
            control.db.one("SELECT 1 AS x")
        except Exception:                                    # noqa: BLE001
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    @app.put("/v1/supplier")
    def put_supplier(body: SupplierIn, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        payload = body.model_dump()
        payload["udyam_registration_date"] = body.udyam_registration_date.isoformat() if body.udyam_registration_date else None
        from .validate import clean_supplier
        clean, errors = clean_supplier(payload, today_fn(), strict=False)
        if errors:
            raise HTTPException(status_code=422, detail=errors)
        store.set_supplier(clean)
        return {"status": "saved"}

    @app.get("/v1/connector/ping")
    def ping(x_api_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        ctx = control.authenticate(x_api_key)
        if ctx is None:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        return {"tenant": ctx.name, "server_date": today_fn().isoformat(), "min_connector_version": MIN_CONNECTOR_VERSION}

    @app.post("/v1/connector/sync")
    def sync(body: SyncIn, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if len(body.records) > MAX_RECORDS:
            raise HTTPException(status_code=413, detail=f"max {MAX_RECORDS} records per sync")
        return ingest_sync(store, body.model_dump())

    @app.get("/v1/invoices")
    def list_invoices(as_of: Optional[date] = Query(default=None), include_paid: bool = False, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        day = as_of or today_fn()
        sup = store.get_supplier()
        items = [invoice_view(r, sup, day, rates) for r in store.list_invoices(include_paid)]
        return {"as_of": day.isoformat(), "count": len(items), "invoices": items}

    @app.get("/v1/invoices/{invoice_id}")
    def get_invoice(invoice_id: str, as_of: Optional[date] = Query(default=None), store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        row = store.get_invoice(invoice_id)
        if row is None:
            raise HTTPException(status_code=404, detail="invoice not found")
        return invoice_view(row, store.get_supplier(), as_of or today_fn(), rates)

    @app.put("/v1/buyers/{buyer_id}/agreement")
    def set_agreement(buyer_id: str, body: AgreementIn, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if not store.set_buyer_agreement(buyer_id, body.has_written_agreement, actor="seller", days=min(max(body.agreed_days or 45, 1), 45)):
            raise HTTPException(status_code=404, detail="buyer not found")
        return {"buyer_id": buyer_id, "has_written_agreement": body.has_written_agreement}

    @app.get("/v1/syncs")
    def syncs(store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        return {"syncs": store.list_syncs()}

    # ---------------- seller side: buyer links
    @app.post("/v1/invoices/{invoice_id}/link")
    def create_link(invoice_id: str, ttl_days: Optional[int] = Query(default=None, ge=1, le=365), store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        row = store.get_invoice(invoice_id)
        if row is None:
            raise HTTPException(status_code=404, detail="invoice not found")
        now = now_fn()
        expires = now + timedelta(days=ttl_days or link_ttl_days)
        link_id = store.create_link(invoice_id, expires.isoformat(timespec="seconds"))
        token = signer.issue(store.tid, link_id, int(expires.timestamp()))
        store.audit("seller", "link.create", invoice_id)
        return {"link_id": link_id, "url": f"{public_base_url}/p/{token}", "expires_at": expires.isoformat(timespec="seconds")}

    @app.get("/v1/invoices/{invoice_id}/links")
    def list_links(invoice_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if store.get_invoice(invoice_id) is None:
            raise HTTPException(status_code=404, detail="invoice not found")
        return {"links": [{k: l[k] for k in ("id", "created_at", "expires_at", "revoked", "view_count", "last_viewed_at")} for l in store.list_links(invoice_id)]}

    @app.delete("/v1/invoices/{invoice_id}/links")
    def revoke_links(invoice_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if store.get_invoice(invoice_id) is None:
            raise HTTPException(status_code=404, detail="invoice not found")
        n = store.revoke_links(invoice_id)
        store.audit("seller", "link.revoke", invoice_id)
        return {"revoked": n}

    @app.get("/v1/invoices/{invoice_id}/events")
    def events(invoice_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if store.get_invoice(invoice_id) is None:
            raise HTTPException(status_code=404, detail="invoice not found")
        return {"events": store.events_for_invoice(invoice_id)}

    @app.put("/v1/invoices/{invoice_id}/dispute")
    def resolve_dispute(invoice_id: str, body: DisputeResolveIn, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if store.get_invoice(invoice_id) is None:
            raise HTTPException(status_code=404, detail="invoice not found")
        ok = body.resolved and store.resolve_dispute(invoice_id)
        if ok:
            store.add_event(invoice_id, None, "dispute_resolved", {}, at=now_fn().isoformat(timespec="seconds"))
            store.audit("seller", "dispute.resolve", invoice_id)
        return {"resolved": bool(ok)}

    # ---------------- bank credits and matching
    def _match_view(m: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": m["id"], "status": m["status"], "score": m["score"], "method": m["method"], "reasons": m["reasons"],
                "confirmed_by": m["confirmed_by"], "allocations": [{"invoice_id": a["invoice_id"], "invoice_number": a.get("invoice_number"), "amount": to_rupees(a["paise"])} for a in m["allocations"]]}

    def _credit_view(store: TenantStore, c: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": c["id"], "date": c["txn_date"], "amount": to_rupees(c["amount_paise"]), "matched": to_rupees(c["matched_paise"]), "status": c["status"],
                "utr": c["utr"], "narration": c["narration"], "payer_name": c["payer_name"], "matches": [_match_view(m) for m in store.matches_for_credit(c["id"]) if m["status"] != "rejected"]}

    def _recon(fn, *a, **k):
        try:
            return fn(*a, **k)
        except reconcile.ReconError as e:
            raise HTTPException(status_code=e.status, detail=str(e))

    @app.post("/v1/credits/import")
    def import_credits(body: ImportIn, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if body.format == "csv":
            if not body.text:
                raise HTTPException(status_code=422, detail="text required for csv")
            parsed = parse_csv(body.text)
        elif body.format == "rows":
            parsed = parse_rows(body.rows or [], "rows")
        else:
            raise HTTPException(status_code=422, detail="format must be csv or rows")
        if len(parsed.credits) > MAX_RECORDS:
            raise HTTPException(status_code=413, detail=f"max {MAX_RECORDS} credits per import")
        return reconcile.import_credits(store, parsed, auto_confirm_at=auto_confirm_at)

    @app.get("/v1/credits")
    def list_credits(status: Optional[str] = None, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        return {"credits": [_credit_view(store, c) for c in store.list_credits(status)]}

    @app.get("/v1/credits/{credit_id}")
    def get_credit(credit_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        c = store.get_credit(credit_id)
        if c is None:
            raise HTTPException(status_code=404, detail="credit not found")
        return _credit_view(store, c)

    @app.post("/v1/credits/{credit_id}/suggest")
    def resuggest(credit_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if store.get_credit(credit_id) is None:
            raise HTTPException(status_code=404, detail="credit not found")
        reconcile.suggest_for_credit(store, credit_id)
        return _credit_view(store, store.get_credit(credit_id))

    @app.post("/v1/credits/{credit_id}/manual")
    def manual_match(credit_id: str, body: ManualIn, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        allocs = [{"invoice_id": a.invoice_id, "paise": to_paise(a.amount)} for a in body.allocations]
        for a in allocs:
            inv = store.get_invoice(a["invoice_id"])
            a["invoice_number"] = inv["invoice_number"] if inv else None
        _recon(reconcile.assign_manually, store, credit_id, allocs, "seller", now_fn().isoformat(timespec="seconds"))
        return _credit_view(store, store.get_credit(credit_id))

    @app.post("/v1/credits/{credit_id}/ignore")
    def ignore(credit_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        _recon(reconcile.ignore_credit, store, credit_id, "seller")
        return {"status": "ignored"}

    @app.post("/v1/matches/{match_id}/confirm")
    def confirm_match(match_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        m = _recon(reconcile.confirm, store, match_id, "seller", now_fn().isoformat(timespec="seconds"))
        return _credit_view(store, store.get_credit(m["credit_id"]))

    @app.post("/v1/matches/{match_id}/reject")
    def reject_match(match_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        m = _recon(reconcile.reject, store, match_id, "seller")
        return _credit_view(store, store.get_credit(m["credit_id"]))

    @app.post("/v1/matches/{match_id}/unconfirm")
    def unconfirm_match(match_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        m = _recon(reconcile.unconfirm, store, match_id, "seller")
        return _credit_view(store, store.get_credit(m["credit_id"]))

    @app.get("/v1/reconciliation/summary")
    def recon_summary(store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        return store.reconciliation_summary()

    # ---------------- consent, pause, reminders
    @app.put("/v1/buyers/{buyer_id}/opt-in")
    def opt_in(buyer_id: str, body: OptInIn, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if len(body.source.strip()) < 3 or not store.set_opt_in(buyer_id, body.source.strip()[:80], now_fn().isoformat(timespec="seconds")):
            raise HTTPException(status_code=404 if len(body.source.strip()) >= 3 else 422, detail="buyer not found or source missing")
        return {"buyer_id": buyer_id, "opted_in": True}

    @app.delete("/v1/buyers/{buyer_id}/opt-in")
    def opt_out(buyer_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if not store.opt_out(buyer_id, now_fn().isoformat(timespec="seconds")):
            raise HTTPException(status_code=404, detail="buyer not found")
        return {"buyer_id": buyer_id, "opted_in": False}

    @app.put("/v1/buyers/{buyer_id}/language")
    def set_language(buyer_id: str, body: LanguageIn, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if body.language not in ("en", "hi", "kn") or not store.set_language(buyer_id, body.language):
            raise HTTPException(status_code=422, detail="language must be en, hi or kn, and the buyer must exist")
        return {"buyer_id": buyer_id, "language": body.language}

    @app.post("/v1/invoices/{invoice_id}/pause")
    def pause_invoice(invoice_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if not store.set_invoice_paused(invoice_id, True):
            raise HTTPException(status_code=404, detail="invoice not found")
        store.audit("seller", "invoice.pause", invoice_id)
        return {"paused": True}

    @app.post("/v1/invoices/{invoice_id}/resume")
    def resume_invoice(invoice_id: str, store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if not store.set_invoice_paused(invoice_id, False):
            raise HTTPException(status_code=404, detail="invoice not found")
        store.audit("seller", "invoice.resume", invoice_id)
        return {"paused": False}

    @app.post("/v1/reminders/pause")
    def pause_all(store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        store.set_setting("reminders_paused", "1")
        store.audit("seller", "reminders.pause", "")
        return {"paused": True}

    @app.post("/v1/reminders/resume")
    def resume_all(store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        store.set_setting("reminders_paused", "0")
        store.audit("seller", "reminders.resume", "")
        return {"paused": False}

    @app.get("/v1/reminders/preview")
    def preview(store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        return asdict(engine.run(store, dry_run=True))

    @app.post("/v1/reminders/run")
    def run_reminders(store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        if engine.sender is None:
            raise HTTPException(status_code=503, detail="no WhatsApp sender configured")
        return asdict(engine.run(store))

    @app.get("/v1/reminders/stats")
    def reminder_stats(store: TenantStore = Depends(tenant)) -> Dict[str, Any]:
        return store.reminder_stats()

    # ---------------- WhatsApp delivery webhook (provider calls this; signed)
    @app.get("/v1/webhooks/whatsapp")
    def wa_verify(request: Request):
        q = request.query_params
        if wa_verify_token and q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""), wa_verify_token):
            return HTMLResponse(q.get("hub.challenge", ""))
        raise HTTPException(status_code=403, detail="verification failed")

    @app.post("/v1/webhooks/whatsapp")
    async def wa_status(request: Request) -> Dict[str, Any]:
        raw = await request.body()
        if not wa_app_secret:
            raise HTTPException(status_code=403, detail="webhook not configured")
        want = "sha256=" + hmac.new(wa_app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, request.headers.get("X-Hub-Signature-256", "")):
            raise HTTPException(status_code=403, detail="bad signature")
        try:
            data = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="bad json")
        rank = {"sent": 1, "delivered": 2, "read": 3}
        updated = 0
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                for st in change.get("value", {}).get("statuses", []):
                    mid, status = st.get("id"), st.get("status")
                    tid = control.tenant_for_message(mid) if mid else None
                    ctx = control.context_by_id(tid) if tid else None
                    if ctx is None:
                        continue
                    store = control.store_for(ctx)
                    ev = store.event_by_provider_id(mid)
                    if ev is None:
                        continue
                    if status in rank and rank[status] > rank.get(ev["status"], 0):
                        store.update_event(ev["id"], status=status)
                        updated += 1
                    elif status == "failed" and ev["status"] != "read":
                        err = (st.get("errors") or [{}])[0]
                        store.update_event(ev["id"], status="failed", error=f"{err.get('code')}: {str(err.get('title', ''))[:100]}")
                        updated += 1
        return {"updated": updated}

    # ---------------- public buyer page (capability links, no login)
    def _public_ctx(token: str):
        claims = signer.verify(token, int(now_fn().timestamp()))
        if claims is None:
            return None
        ctx = control.context_by_id(claims.tenant_id)
        if ctx is None:
            return None
        store = control.store_for(ctx)
        link = store.get_link(claims.link_id)
        if link is None or link["revoked"] or link["expires_at"] <= now_fn().isoformat(timespec="seconds"):
            return None
        row = store.get_invoice(link["invoice_id"])
        return (store, link, row) if row else None

    def _html(body: str, status: int = 200) -> HTMLResponse:
        assert len(body.encode()) < MAX_PAGE_BYTES
        return HTMLResponse(body, status_code=status, headers=security_headers())

    def _lang(request: Request, row: Optional[Dict[str, Any]] = None) -> str:
        """?lang= wins (the buyer's own choice from the language bar), else the language saved for this buyer, else English."""
        q = request.query_params.get("lang")
        return norm_lang(q if q else (row or {}).get("buyer_language"))

    def _not_valid(lang: str = "en") -> HTMLResponse:
        return _html(render_notice_page(_tr(lang, "bad_link_t"), _tr(lang, "bad_link_b"), lang), 404)

    def _page(store: TenantStore, row: Dict[str, Any], token: str, notice: str = "", lang: str = "en") -> str:
        sup = store.get_supplier()
        today = today_fn()
        view = invoice_view(row, sup, today, rates)
        clock = view["clock"]
        blocked = {"REGISTERED_AFTER_SUPPLY", "SUPPLIER_PROFILE_INCOMPLETE"} & set(clock["review_flags"])
        status = "PAID" if row["status"] == "PAID" or view["outstanding"] <= 0 else ("DISPUTED" if row["status"] == "DISPUTED" else "OPEN")
        due = date.fromisoformat(clock["due_date"])
        fy_end = financial_year_end(due)
        return render_invoice_page(PageData(
            token=token, today=today, seller_name=(sup or {}).get("business_name") or "your supplier", invoice_number=row["invoice_number"],
            invoice_date=date.fromisoformat(row["invoice_date"]), amount=view["amount"], outstanding=view["outstanding"], due_date=due,
            days_remaining=clock["days_remaining"], overdue_days=clock["overdue_days"], status=status,
            statutory_ok=bool(clock["covered"]) and not blocked, fy_label=financial_year(due), fy_end_year=fy_end.year,
            interest=clock["interest"] if show_interest else None, upi_vpa=(sup or {}).get("upi_vpa"),
            promised_date=date.fromisoformat(row["promised_date"]) if row.get("promised_date") else None,
            scan_url=scan_url, privacy_url=privacy_url, grievance_email=grievance_email, notice=notice, lang=lang))

    @app.get("/p/{token}", response_class=HTMLResponse)
    def buyer_page(token: str, request: Request, done: str = "") -> HTMLResponse:
        found = _public_ctx(token)
        if found is None:
            return _not_valid(_lang(request))
        store, link, row = found
        store.touch_link(link["id"])
        store.add_event(row["id"], link["id"], "view", {}, at=now_fn().isoformat(timespec="seconds"))
        return _html(_page(store, row, token, done if done in ("promise", "dispute") else "", _lang(request, row)))

    async def _form(request: Request) -> Dict[str, str]:
        raw = (await request.body())[:4000].decode("utf-8", "ignore")
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def _limited(store: TenantStore, link_id: str) -> bool:
        since = (now_fn() - timedelta(hours=1)).isoformat(timespec="seconds")
        return store.count_events(link_id, ["promise", "dispute"], since) >= 5

    @app.post("/p/{token}/promise")
    async def buyer_promise(token: str, request: Request):
        found = _public_ctx(token)
        if found is None:
            return _not_valid(_lang(request))
        store, link, row = found
        lang = _lang(request, row)
        if row["status"] in ("PAID", "DISPUTED") or row["outstanding_paise"] <= 0:
            return RedirectResponse(f"/p/{token}?lang={lang}", status_code=303)
        if _limited(store, link["id"]):
            return _html(render_notice_page(_tr(lang, "many_t"), _tr(lang, "many_b"), lang), 429)
        form = await _form(request)
        today = today_fn()
        try:
            d = date.fromisoformat(form.get("date", ""))
        except ValueError:
            return _html(render_notice_page(_tr(lang, "bad_date_t"), _tr(lang, "bad_date_1"), lang), 400)
        if not (today < d <= today + timedelta(days=PROMISE_MAX_DAYS)):
            return _html(render_notice_page(_tr(lang, "bad_date_t"), _tr(lang, "bad_date_2", n=PROMISE_MAX_DAYS), lang), 400)
        store.set_promise(row["id"], d.isoformat())
        store.add_event(row["id"], link["id"], "promise", {"date": d.isoformat()}, at=now_fn().isoformat(timespec="seconds"))
        return RedirectResponse(f"/p/{token}?done=promise&lang={lang}", status_code=303)

    @app.post("/p/{token}/dispute")
    async def buyer_dispute(token: str, request: Request):
        found = _public_ctx(token)
        if found is None:
            return _not_valid(_lang(request))
        store, link, row = found
        lang = _lang(request, row)
        if row["status"] in ("PAID", "DISPUTED") or row["outstanding_paise"] <= 0:
            return RedirectResponse(f"/p/{token}?lang={lang}", status_code=303)
        if _limited(store, link["id"]):
            return _html(render_notice_page(_tr(lang, "many_t"), _tr(lang, "many_b"), lang), 429)
        form = await _form(request)
        reason = form.get("reason", "")
        if reason not in DISPUTE_REASONS:
            return _html(render_notice_page(_tr(lang, "reason_t"), _tr(lang, "reason_b"), lang), 400)
        store.set_disputed(row["id"])
        store.add_event(row["id"], link["id"], "dispute", {"reason": reason, "note": form.get("note", "")[:500]}, at=now_fn().isoformat(timespec="seconds"))
        return RedirectResponse(f"/p/{token}?done=dispute&lang={lang}", status_code=303)

    mount_scan(app, control, now_fn=now_fn, today_fn=today_fn, rates=rates, secret=token_secret or load_secret(data_dir), privacy_url=privacy_url)
    mount_owner_ui(app, UiContext(control, engine, rates, signer, SessionSigner(token_secret or load_secret(data_dir)), now_fn, today_fn, public_base_url,
                                  invoice_view, tuple(approved_languages), link_ttl_days))
    return app
