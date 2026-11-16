"""Owner screen routes (/app): sign in with the company access key, then review collections, invoices, payments and settings.

Server-rendered; cookie session (HttpOnly, SameSite=Strict) plus a CSRF token on every POST; login attempts are rate limited.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import reconcile
from .bank_import import parse_csv
from .cadence_plan import STEPS
from .owner_pages import (STEP_NAMES, dashboard_page, invoice_page, login_page, not_found_page, owner_headers, payments_page, settings_page)
from .buyer_page import fmt_date
from .store import ControlPlane, TenantStore, to_paise, to_rupees
from .tokens import SessionSigner, TokenSigner

COOKIE = "dc_session"
SESSION_HOURS = 12
STALE_HOURS = 36
LOGIN_LIMIT, LOGIN_WINDOW_S = 10, 900
ROW_CAP = 200


@dataclass
class UiContext:
    control: ControlPlane
    engine: Any
    rates: Any
    signer: TokenSigner
    sessions: SessionSigner
    now_fn: Callable[[], datetime]
    today_fn: Callable[[], date]
    public_base_url: str
    invoice_view: Callable[..., Dict[str, Any]]
    approved_languages: tuple = ("en",)
    link_ttl_days: int = 90


def _badges(row: Dict[str, Any], v: Dict[str, Any], today: date) -> List[tuple]:
    c, out = v["clock"], []
    if row["status"] == "DISPUTED":
        out.append(("Disputed", "bad"))
    if row.get("promised_date") and row["promised_date"] >= today.isoformat():
        out.append((f"Promise {fmt_date(date.fromisoformat(row['promised_date']))}", "warn"))
    if row["paused"]:
        out.append(("Paused", ""))
    if not (row.get("opt_in_at") and not row.get("opted_out_at")):
        out.append(("No consent", "warn"))
    if not c["covered"]:
        out.append(("Not covered", ""))
    elif c["disallowance_exposure"]:
        out.append(("43B(h) exposure", "warn"))
    if v["unposted_payments"]:
        out.append(("Part paid, awaiting Tally", "ok"))
    return out


def _next_step(store: TenantStore, row: Dict[str, Any], c: Dict[str, Any], today: date) -> str:
    if not c["covered"] or c["outstanding"] <= 0:
        return ""
    sent = {e["step"] for e in store.events_for_steps(row["id"])}
    due = date.fromisoformat(c["due_date"])
    for step, off in STEPS:
        if step not in sent:
            when = due + timedelta(days=off)
            return f"{STEP_NAMES[step]}: due now" if when <= today else f"{STEP_NAMES[step]} on {fmt_date(when)}"
    return "All reminders sent"


def dashboard_data(store: TenantStore, u: UiContext, flt: str) -> Dict[str, Any]:
    today, sup = u.today_fn(), store.get_supplier()
    incomplete = sup is None or (sup.get("category") or "UNKNOWN") == "UNKNOWN" or (sup.get("activity") or "UNKNOWN") == "UNKNOWN"
    rows, tiles = [], {"outstanding": 0.0, "overdue_amount": 0.0, "overdue_count": 0, "due7_amount": 0.0, "interest": 0.0, "exposure_count": 0}
    for r in store.list_invoices(include_paid=False):
        v = u.invoice_view(r, sup, today, u.rates)
        c = v["clock"]
        if v["outstanding"] <= 0:
            continue
        b = _badges(r, v, today)
        tiles["outstanding"] += v["outstanding"]
        if c["overdue_days"] > 0:
            tiles["overdue_amount"] += v["outstanding"]; tiles["overdue_count"] += 1
        elif 0 <= c["days_remaining"] <= 7:
            tiles["due7_amount"] += v["outstanding"]
        tiles["interest"] += c["interest"]
        tiles["exposure_count"] += 1 if c["disallowance_exposure"] else 0
        rows.append({"id": r["id"], "number": r["invoice_number"], "buyer": r["buyer_name"], "invoice_date": date.fromisoformat(r["invoice_date"]),
                     "outstanding": v["outstanding"], "due_date": date.fromisoformat(c["due_date"]), "days_remaining": c["days_remaining"],
                     "overdue_days": c["overdue_days"], "badges": b, "next_step": _next_step(store, r, c, today),
                     "disputed": r["status"] == "DISPUTED", "attention": r["status"] == "DISPUTED" or any(t == "No consent" for t, _ in b)})
    rows.sort(key=lambda x: (-x["overdue_days"], x["due_date"]))
    counts = {"all": len(rows), "overdue": sum(1 for r in rows if r["overdue_days"] > 0),
              "due7": sum(1 for r in rows if r["overdue_days"] <= 0 and 0 <= r["days_remaining"] <= 7),
              "disputed": sum(1 for r in rows if r["disputed"]), "attention": sum(1 for r in rows if r["attention"])}
    keep = {"all": lambda r: True, "overdue": lambda r: r["overdue_days"] > 0, "due7": lambda r: r["overdue_days"] <= 0 and 0 <= r["days_remaining"] <= 7,
            "disputed": lambda r: r["disputed"], "attention": lambda r: r["attention"]}[flt if flt in counts else "all"]
    shown = [r for r in rows if keep(r)]
    last = (store.list_syncs(1) or [None])[0]
    stale = bool(last) and datetime.fromisoformat(last["finished_at"]) < u.now_fn() - timedelta(hours=STALE_HOURS)
    waiting = sum(1 for cr in store.list_credits() if cr["status"] in ("unmatched", "suggested", "partial"))
    return {"as_of": today, "tiles": tiles, "rows": shown[:ROW_CAP], "shown_of": len(shown), "counts": counts, "filter": flt if flt in counts else "all",
            "last_sync": last, "profile_incomplete": incomplete, "stale": stale, "credits_waiting": waiting, "paused": store.get_setting("reminders_paused") == "1", "reminders": store.reminder_stats()}


def invoice_data(store: TenantStore, u: UiContext, row: Dict[str, Any]) -> Dict[str, Any]:
    today = u.today_fn()
    v = u.invoice_view(row, store.get_supplier(), today, u.rates)
    link_url = link_exp = None
    views = 0
    for l in reversed(store.list_links(row["id"])):
        if not l["revoked"] and l["expires_at"] > u.now_fn().isoformat(timespec="seconds"):
            tok = u.signer.issue(store.tid, l["id"], int(datetime.fromisoformat(l["expires_at"]).timestamp()))
            link_url, link_exp, views = f"{u.public_base_url}/p/{tok}", l["expires_at"], l["view_count"]
            break
    tl = []
    for e in store.events_for_steps(row["id"]):
        tl.append({"at": e["created_at"] or "", "text": f"Reminder {STEP_NAMES.get(e['step'].split(':')[0], e['step'])}: {e['status']}"})
    for e in store.events_for_invoice(row["id"]):
        p = e["payload"]
        text = {"view": "Buyer opened the link", "promise": f"Buyer promised to pay on {p.get('date')}", "dispute": f"Buyer raised a dispute: {p.get('reason')}",
                "dispute_resolved": "Dispute marked resolved"}.get(e["kind"], e["kind"])
        tl.append({"at": e["at"], "text": text})
    tl.sort(key=lambda x: x["at"], reverse=True)
    return {"view": v, "badges": _badges(row, v, today), "opted_in": bool(row.get("opt_in_at") and not row.get("opted_out_at")), "paused": bool(row["paused"]),
            "agreement": bool(row["buyer_agreement"]), "link_url": link_url, "link_expires": link_exp or "", "link_views": views,
            "next_step": _next_step(store, row, v["clock"], today), "timeline": tl[:60]}


def credit_view(store: TenantStore, c: Dict[str, Any]) -> Dict[str, Any]:
    ms = [{"id": m["id"], "status": m["status"], "score": m["score"], "reasons": m["reasons"],
           "allocations": [{"invoice_number": a.get("invoice_number"), "amount": to_rupees(a["paise"])} for a in m["allocations"]]}
          for m in store.matches_for_credit(c["id"]) if m["status"] not in ("rejected", "reversed")]
    return {"id": c["id"], "date": c["txn_date"], "amount": to_rupees(c["amount_paise"]), "matched": to_rupees(c["matched_paise"]), "status": c["status"],
            "narration": c["narration"], "matches": ms}


def _days(text: Optional[str]) -> int:
    try:
        return min(max(int(text or 45), 1), 45)
    except ValueError:
        return 45


def mount_owner_ui(app: FastAPI, u: UiContext) -> None:
    secure = u.public_base_url.startswith("https")
    attempts: Dict[str, List[float]] = {}

    def html(body: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(body, status_code=status, headers=owner_headers())

    def back(path: str, msg: str = "", err: str = "") -> RedirectResponse:
        q = f"?msg={msg}" if msg else (f"?err={err}" if err else "")
        return RedirectResponse(path + q, status_code=303)

    def auth(request: Request):
        tok = u.sessions.verify(request.cookies.get(COOKIE), int(u.now_fn().timestamp()))
        if tok is None:
            return None
        ctx = u.control.context_by_id(tok[0])
        return None if ctx is None else (u.control.store_for(ctx), ctx, u.sessions.csrf(tok[1]))

    async def form(request: Request) -> Dict[str, str]:
        raw = (await request.body())[:300_000].decode("utf-8", "ignore")
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    async def guarded(request: Request):
        a = auth(request)
        if a is None:
            return RedirectResponse("/app/login", status_code=303)
        f = await form(request)
        if not hmac.compare_digest(f.get("csrf", ""), a[2]):
            return html(not_found_page().replace("Not found", "Request not allowed"), 403)
        return a + (f,)

    # ---- login / logout
    @app.get("/app/login", response_class=HTMLResponse)
    def login_form() -> HTMLResponse:
        return html(login_page())

    @app.post("/app/login")
    async def login(request: Request):
        ip, now = (request.client.host if request.client else "?"), u.now_fn().timestamp()
        recent = [t for t in attempts.get(ip, []) if t > now - LOGIN_WINDOW_S]
        if len(recent) >= LOGIN_LIMIT:
            return html(login_page(limited=True), 429)
        ctx = u.control.authenticate((await form(request)).get("key", "").strip())
        if ctx is None:
            attempts[ip] = recent + [now]
            return html(login_page(error=True), 401)
        r = RedirectResponse("/app", status_code=303)
        r.set_cookie(COOKIE, u.sessions.issue(ctx.tenant_id, int(now) + SESSION_HOURS * 3600), max_age=SESSION_HOURS * 3600, httponly=True, samesite="strict", secure=secure, path="/app")
        return r

    @app.post("/app/logout")
    async def logout(request: Request):
        g = await guarded(request)
        if isinstance(g, (HTMLResponse, RedirectResponse)):
            return g
        r = RedirectResponse("/app/login", status_code=303)
        r.delete_cookie(COOKIE, path="/app")
        return r

    # ---- pages
    def page(request: Request, builder, data_fn):
        a = auth(request)
        if a is None:
            return RedirectResponse("/app/login", status_code=303)
        store, ctx, csrf = a
        q = request.query_params
        return html(builder(data_fn(store), csrf, ctx.name, q.get("msg", ""), q.get("err", "")))

    @app.get("/app", response_class=HTMLResponse)
    def dashboard(request: Request):
        return page(request, dashboard_page, lambda s: dashboard_data(s, u, request.query_params.get("filter", "all")))

    @app.get("/app/payments", response_class=HTMLResponse)
    def payments(request: Request):
        return page(request, payments_page, lambda s: {"summary": s.reconciliation_summary(), "open_invoices": s.matching_invoices(),
                                                       "credits": [credit_view(s, c) for c in sorted(s.list_credits(), key=lambda c: (c["status"] == "matched", c["status"] == "ignored", c["txn_date"]))]})

    def settings_data(s, values=None, errors=None) -> Dict[str, Any]:
        from .validate import coverage_preview
        prof = values if values is not None else (s.get_supplier() or {})
        return {"reminders": s.reminder_stats(), "paused": s.get_setting("reminders_paused") == "1", "sender": u.engine.sender is not None,
                "languages": list(u.approved_languages), "preview": __import__("dataclasses").asdict(u.engine.run(s, dry_run=True)), "syncs": s.list_syncs(10),
                "profile": prof, "profile_errors": errors or {}, "profile_preview": coverage_preview(prof, s.list_invoices(include_paid=True)) if prof else coverage_preview({}, [])}

    @app.get("/app/settings", response_class=HTMLResponse)
    def settings(request: Request):
        return page(request, settings_page, settings_data)

    @app.post("/app/settings/business")
    async def save_business(request: Request):
        from .validate import clean_supplier
        g = await guarded(request)
        if isinstance(g, (HTMLResponse, RedirectResponse)):
            return g
        store, ctx, csrf, f = g
        clean, errors = clean_supplier(f, u.today_fn(), strict=True)
        if errors:
            shown = dict(f)                                                   # keep exactly what was typed so nothing is lost
            return html(settings_page(settings_data(store, shown, errors), csrf, ctx.name, "", "fields"), 400)
        store.set_supplier(clean, actor="owner screen")
        return back("/app/settings", "saved")

    @app.get("/app/invoices/{invoice_id}", response_class=HTMLResponse)
    def invoice(invoice_id: str, request: Request):
        a = auth(request)
        if a is None:
            return RedirectResponse("/app/login", status_code=303)
        store, ctx, csrf = a
        row = store.get_invoice(invoice_id)
        if row is None:
            return html(not_found_page(), 404)
        q = request.query_params
        return html(invoice_page(invoice_data(store, u, row), csrf, ctx.name, q.get("msg", ""), q.get("err", "")))

    # ---- invoice actions
    async def invoice_action(request: Request, invoice_id: str, fn):
        g = await guarded(request)
        if isinstance(g, (HTMLResponse, RedirectResponse)):
            return g
        store, _, _, f = g
        row = store.get_invoice(invoice_id)
        if row is None:
            return html(not_found_page(), 404)
        res = fn(store, row, f)
        return back(f"/app/invoices/{invoice_id}", *(res if isinstance(res, tuple) else (res,)))

    @app.post("/app/invoices/{invoice_id}/agreement")
    async def act_agreement(invoice_id: str, request: Request):
        return await invoice_action(request, invoice_id, lambda s, r, f: (s.set_buyer_agreement(r["buyer_id"], f.get("value") == "yes", "owner screen", _days(f.get("days"))), "agreement")[1])

    @app.post("/app/invoices/{invoice_id}/optin")
    async def act_optin(invoice_id: str, request: Request):
        def fn(s, r, f):
            if f.get("opt") == "no":
                s.opt_out(r["buyer_id"], u.now_fn().isoformat(timespec="seconds")); return "optout"
            if f.get("confirm") != "on":
                return ("", "consent")
            s.set_opt_in(r["buyer_id"], "owner screen: seller confirms the buyer agreed", u.now_fn().isoformat(timespec="seconds")); return "optin"
        return await invoice_action(request, invoice_id, fn)

    @app.post("/app/invoices/{invoice_id}/pause")
    async def act_pause(invoice_id: str, request: Request):
        return await invoice_action(request, invoice_id, lambda s, r, f: (s.set_invoice_paused(r["id"], f.get("value") == "yes"), "saved")[1])

    @app.post("/app/invoices/{invoice_id}/link")
    async def act_link(invoice_id: str, request: Request):
        def fn(s, r, f):
            s.create_link(r["id"], (u.now_fn() + timedelta(days=u.link_ttl_days)).isoformat(timespec="seconds")); s.audit("owner screen", "link.create", r["id"]); return "link"
        return await invoice_action(request, invoice_id, fn)

    @app.post("/app/invoices/{invoice_id}/link/revoke")
    async def act_revoke(invoice_id: str, request: Request):
        return await invoice_action(request, invoice_id, lambda s, r, f: (s.revoke_links(r["id"]), s.audit("owner screen", "link.revoke", r["id"]), "revoked")[2])

    @app.post("/app/invoices/{invoice_id}/dispute/resolve")
    async def act_resolve(invoice_id: str, request: Request):
        def fn(s, r, f):
            if s.resolve_dispute(r["id"]):
                s.add_event(r["id"], None, "dispute_resolved", {}, at=u.now_fn().isoformat(timespec="seconds")); s.audit("owner screen", "dispute.resolve", r["id"])
            return "resolved"
        return await invoice_action(request, invoice_id, fn)

    # ---- payments actions
    async def pay_action(request: Request, fn, ok_msg: str):
        g = await guarded(request)
        if isinstance(g, (HTMLResponse, RedirectResponse)):
            return g
        store, _, _, f = g
        try:
            fn(store, f)
        except reconcile.ReconError as e:
            return back("/app/payments", err="invalid" if e.status == 422 else "conflict")
        except ValueError:
            return back("/app/payments", err="invalid")
        return back("/app/payments", ok_msg)

    @app.post("/app/credits/import")
    async def act_import(request: Request):
        return await pay_action(request, lambda s, f: reconcile.import_credits(s, parse_csv(f.get("text", ""))), "imported")

    @app.post("/app/matches/{match_id}/{verb}")
    async def act_match(match_id: str, verb: str, request: Request):
        fns = {"confirm": (lambda s, f: reconcile.confirm(s, match_id, "owner screen", u.now_fn().isoformat(timespec="seconds")), "confirmed"),
               "reject": (lambda s, f: reconcile.reject(s, match_id, "owner screen"), "rejected"),
               "unconfirm": (lambda s, f: reconcile.unconfirm(s, match_id, "owner screen"), "undone")}
        if verb not in fns:
            return html(not_found_page(), 404)
        return await pay_action(request, *fns[verb])

    @app.post("/app/credits/{credit_id}/manual")
    async def act_manual(credit_id: str, request: Request):
        def fn(s, f):
            amount = float(f.get("amount", "").replace(",", ""))
            inv = s.get_invoice(f.get("invoice_id", ""))
            if inv is None:
                raise reconcile.ReconError("invoice not found", 422)
            reconcile.assign_manually(s, credit_id, [{"invoice_id": inv["id"], "invoice_number": inv["invoice_number"], "paise": to_paise(amount)}], "owner screen", u.now_fn().isoformat(timespec="seconds"))
        return await pay_action(request, fn, "manual")

    @app.post("/app/credits/{credit_id}/ignore")
    async def act_ignore(credit_id: str, request: Request):
        return await pay_action(request, lambda s, f: reconcile.ignore_credit(s, credit_id, "owner screen"), "ignored")

    # ---- settings
    @app.post("/app/settings/reminders")
    async def act_reminders(request: Request):
        g = await guarded(request)
        if isinstance(g, (HTMLResponse, RedirectResponse)):
            return g
        store, _, _, f = g
        pause = f.get("value") == "pause"
        store.set_setting("reminders_paused", "1" if pause else "0")
        store.audit("owner screen", "reminders.pause" if pause else "reminders.resume", "")
        return back("/app/settings", "paused" if pause else "resumed")
