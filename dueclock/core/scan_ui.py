"""Public exposure-scan pages (/exposure-scan) and the API-key JSON scan (POST /v1/scan).

Privacy: the pasted ledger is processed, kept for 24 hours under an unguessable link so the visitor can come back, and can be
deleted at any time. Only an email (with consent) is kept beyond that, and only to send the visitor what they asked for.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from html import escape as e
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel

from . import scan as S
from .buyer_page import _shell, fmt_date, inr, security_headers
from .rates import RateTable
from .store import ControlPlane

SCAN_TTL_HOURS = 24
SCANS_PER_HOUR = 10
MAX_BODY = 1_200_000
TABLE_CAP = 300
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


class ApiScanIn(BaseModel):
    format: str = "csv"                     # csv | tally_xml | rows
    text: Optional[str] = None
    rows: Optional[List[Dict[str, Any]]] = None
    as_of: Optional[date] = None
    default_agreed_days: int = 0


def _n(n: int) -> str:
    return f"{n} bill" + ("" if n == 1 else "s")


def _label(status: str) -> str:
    return S.LABEL.get(status, status)


def form_page(msg: str = "", err: str = "", privacy_url: str = "/privacy", today: Optional[date] = None) -> str:
    banner = ""
    if msg == "deleted":
        banner = '<div class="c ok">Your scan has been deleted.</div>'
    if err:
        banner = f'<div class="c warn" role="alert">{e(err)}</div>'
    body = (
        f'<h1>Free year-end exposure scan</h1>{banner}'
        '<div class="c">Paste your list of unpaid vendor bills and see which could lose their tax deduction under Section 43B(h) if still unpaid at the financial year end. '
        'Micro and small manufacturers and service providers must be paid within 15 days, or within the days agreed in writing (at most 45).</div>'
        '<form method="post" action="/exposure-scan" class="c"><label for="f">Format</label><select id="f" name="format"><option value="csv">CSV (with a header row)</option><option value="tally_xml">Tally XML export (Bills Payable)</option></select>'
        '<label for="l">Your payables list</label><textarea id="l" name="ledger" rows="9" required placeholder="Vendor,Bill No,Bill Date,Amount,Pending,Category,Activity,Credit Days"></textarea>'
        '<p class="s">Columns: Vendor, Bill Date and Amount are needed. Add Pending (or Paid) for part payments. Add Category (Micro or Small) and Activity (Manufacturing or Services) '
        'for vendors you know are covered; add Credit Days where a written agreement exists. Vendors without that information are shown separately as <b>possible</b> exposure.</p>'
        '<label for="d">As of</label><input id="d" name="as_of" type="date"' + (f' value="{today.isoformat()}"' if today else "") + '>'
        '<label for="a">Payment terms when not stated</label><select id="a" name="default_days"><option value="0">No written agreement (15 days)</option><option value="45">Written agreement, 45 days</option></select>'
        '<button type="submit">Run the scan</button></form>'
        f'<footer>We keep your list for {SCAN_TTL_HOURS} hours so you can return to it, then delete it. You can delete it sooner. Computed for your CA to review; not legal advice. '
        f'<a href="{e(privacy_url, quote=True)}">Privacy notice</a></footer>')
    return _shell("Exposure scan", body)


def notice(title: str, message: str) -> str:
    return _shell(title, f'<h1>{e(title)}</h1><div class="c">{e(message)}</div><p><a href="/exposure-scan">Back to the scan</a></p>')


def result_page(sid: str, d: Dict[str, Any], unlocked: bool, privacy_url: str, notice_msg: str = "") -> str:
    s = d["summary"]
    cards = (
        f'<div class="c warn"><h2>At risk if unpaid on {e(fmt_date(date.fromisoformat(d["fy_end"])))}</h2><div class="amt">{e(inr(s["confirmed_at_risk"]))}</div>'
        f'<span class="s">across {_n(s["past_limit"]["bills"] + s["crosses_by_year_end"]["bills"])} from vendors known to be covered ({e(d["fy_label"])})</span></div>'
        f'<div class="c"><table><tr><td>Already past the limit</td><td><b>{e(inr(s["past_limit"]["amount"]))}</b> ({_n(s["past_limit"]["bills"])})</td></tr>'
        f'<tr><td>Will pass the limit before year end</td><td><b>{e(inr(s["crosses_by_year_end"]["amount"]))}</b> ({_n(s["crosses_by_year_end"]["bills"])})</td></tr>'
        f'<tr><td>Possible: vendors not yet verified</td><td><b>{e(inr(s["possible_at_risk"]["amount"]))}</b> ({_n(s["possible_at_risk"]["bills"])}, {s["vendors_unverified"]} vendor{"" if s["vendors_unverified"] == 1 else "s"})</td></tr>'
        f'<tr><td>Vendors known not to be covered</td><td>{s["vendors_not_covered"]}</td></tr></table>'
        f'<p class="s">Possible exposure assumes the 15-day rule for vendors whose Udyam size and activity are not in your list. Check their Udyam certificates to confirm.</p></div>')
    note = f'<div class="c ok">{e(notice_msg)}</div>' if notice_msg else ""
    errs = ("<div class=\"c warn\">" + "<br>".join(e(x) for x in d["errors"][:8]) + "</div>") if d["errors"] else ""
    if not unlocked:
        gate = (f'<div class="c"><h2>See the vendors and bills, and download the list</h2><form method="post" action="/exposure-scan/r/{e(sid)}/unlock"><label for="m">Your work email</label>'
                f'<input id="m" name="email" type="email" required maxlength="120" autocomplete="email"><label><input type="checkbox" name="consent" value="on" style="width:auto"> '
                f'I agree that DueClock may email me about this scan and the year-end deadlines. I can ask for deletion at any time.</label><button type="submit">Unlock the full list</button></form></div>')
        detail = gate
    else:
        vrows = "".join(f'<tr><td>{e(v["vendor"])}</td><td>{e(_label(v["status"]))}</td><td style="text-align:right">{e(inr(v["outstanding"]))}</td>'
                        f'<td style="text-align:right">{e(inr(v["confirmed_at_risk"] or v["possible_at_risk"]))}</td></tr>' for v in d["vendors"][:TABLE_CAP])
        first = "".join(f'<tr><td>{e(p["vendor"])} {e(p["bill_no"])}</td><td>{e(fmt_date(date.fromisoformat(p["due_date"])))}</td><td style="text-align:right">{e(inr(p["outstanding"]))}</td></tr>' for p in s["pay_first"])
        detail = (f'<div class="c"><h2>Pay these first</h2><p class="s">Bills at risk, earliest limit first. A bill still unpaid on the year end after its limit may not be deductible this year.</p>'
                  f'<table><tr><td><b>Bill</b></td><td><b>Limit</b></td><td style="text-align:right"><b>Outstanding</b></td></tr>{first or "<tr><td>None</td><td></td><td></td></tr>"}</table></div>'
                  f'<div class="c"><h2>By vendor</h2><table><tr><td><b>Vendor</b></td><td><b>Status</b></td><td style="text-align:right"><b>Outstanding</b></td><td style="text-align:right"><b>At risk</b></td></tr>{vrows}</table>'
                  f'<p class="s">Showing up to {TABLE_CAP} vendors. The CSV has every bill.</p><a class="b" href="/exposure-scan/r/{e(sid)}/csv">Download CSV</a></div>')
    foot = (f'<form method="post" action="/exposure-scan/r/{e(sid)}/delete" class="c"><p class="s">This page is private to you and is deleted after {SCAN_TTL_HOURS} hours.</p>'
            f'<button type="submit">Delete my scan now</button></form><footer>As of {e(fmt_date(date.fromisoformat(d["as_of"])))}. Computed for your CA to review. This is not legal advice. '
            f'<a href="{e(privacy_url, quote=True)}">Privacy notice</a></footer>')
    return _shell("Your exposure scan", f'<h1>Your exposure scan</h1>{note}{errs}{cards}{detail}{foot}')


def mount_scan(app: FastAPI, control: ControlPlane, *, now_fn: Callable[[], datetime], today_fn: Callable[[], date], rates: RateTable, secret: bytes,
               privacy_url: str = "/privacy") -> None:
    def html(body: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(body, status_code=status, headers=security_headers())

    def ip_hash(request: Request) -> str:
        return hashlib.sha256(secret + (request.client.host if request.client else "?").encode()).hexdigest()[:24]

    def iso(dt: datetime) -> str:
        return dt.isoformat(timespec="seconds")

    def load(sid: str):
        control.purge_scans(iso(now_fn()))
        row = control.get_scan(sid, iso(now_fn()))
        return None if row is None else (row, json.loads(row["result"]))

    @app.get("/exposure-scan", response_class=HTMLResponse)
    def scan_form(request: Request):
        return html(form_page(request.query_params.get("msg", ""), "", privacy_url, today_fn()))

    @app.post("/exposure-scan")
    async def scan_run(request: Request):
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return html(notice("That list is too large", "Please paste up to about 5,000 bills."), 413)
        f = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "ignore"), keep_blank_values=True).items()}
        now = now_fn()
        h = ip_hash(request)
        if control.count_scans_since(h, iso(now - timedelta(hours=1))) >= SCANS_PER_HOUR:
            return html(notice("Too many scans", "Please try again in a little while."), 429)
        try:
            as_of = date.fromisoformat(f["as_of"]) if f.get("as_of") else today_fn()
        except ValueError:
            return html(form_page(err="That date is not valid.", privacy_url=privacy_url, today=today_fn()), 400)
        if f.get("format") == "tally_xml":
            recs, errs = S.parse_payables_xml(f.get("ledger", ""))
        else:
            recs, errs = S.parse_payables_csv(f.get("ledger", ""))
        if not recs:
            return html(form_page(err=(errs[0] if errs else "No bills were found in that list."), privacy_url=privacy_url, today=today_fn()), 400)
        days = 45 if f.get("default_days") == "45" else 0
        result = S.run_scan(recs, as_of, rates, errors=errs, default_agreed_days=days)
        sid = control.save_scan(json.dumps(result.to_dict()), h, iso(now), iso(now + timedelta(hours=SCAN_TTL_HOURS)))
        return RedirectResponse(f"/exposure-scan/r/{sid}", status_code=303)

    @app.get("/exposure-scan/r/{sid}", response_class=HTMLResponse)
    def scan_result(sid: str, request: Request):
        got = load(sid)
        if got is None:
            return html(notice("This scan is not available", "It may have expired or been deleted."), 404)
        row, d = got
        msg = "Thank you. The full list is unlocked." if request.query_params.get("msg") == "unlocked" else ""
        return html(result_page(sid, d, bool(row["unlocked"]), privacy_url, msg))

    @app.post("/exposure-scan/r/{sid}/unlock")
    async def scan_unlock(sid: str, request: Request):
        got = load(sid)
        if got is None:
            return html(notice("This scan is not available", "It may have expired or been deleted."), 404)
        f = {k: v[0] for k, v in parse_qs((await request.body())[:5000].decode("utf-8", "ignore"), keep_blank_values=True).items()}
        email = f.get("email", "").strip()
        if not _EMAIL.match(email) or len(email) > 120 or f.get("consent") != "on":
            return html(result_page(sid, got[1], False, privacy_url, "") .replace("<h1>Your exposure scan</h1>", '<h1>Your exposure scan</h1><div class="c warn" role="alert">Please enter a valid email and tick the consent box.</div>', 1), 400)
        control.add_lead(email.lower(), sid, ip_hash(request), iso(now_fn()))
        control.unlock_scan(sid)
        return RedirectResponse(f"/exposure-scan/r/{sid}?msg=unlocked", status_code=303)

    @app.get("/exposure-scan/r/{sid}/csv")
    def scan_csv(sid: str):
        got = load(sid)
        if got is None:
            return html(notice("This scan is not available", "It may have expired or been deleted."), 404)
        if not got[0]["unlocked"]:
            return html(notice("Unlock first", "Enter your email on the results page to download the list."), 403)
        d = got[1]
        res = S.ScanResult(date.fromisoformat(d["as_of"]), date.fromisoformat(d["fy_end"]), d["fy_label"], d["summary"], d["vendors"], d["bills"], d["errors"])
        return Response(S.to_csv(res), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="exposure-scan.csv"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/exposure-scan/r/{sid}/delete")
    def scan_delete(sid: str):
        control.delete_scan(sid)
        return RedirectResponse("/exposure-scan?msg=deleted", status_code=303)

    @app.post("/v1/scan")
    def api_scan(body: ApiScanIn, x_api_key: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        if control.authenticate(x_api_key) is None:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        if body.format == "csv":
            recs, errs = S.parse_payables_csv(body.text or "")
        elif body.format == "tally_xml":
            recs, errs = S.parse_payables_xml(body.text or "")
        elif body.format == "rows":
            recs, errs = S._records([{k.strip().lower(): v for k, v in r.items()} for r in (body.rows or [])])
        else:
            raise HTTPException(status_code=422, detail="format must be csv, tally_xml or rows")
        if not recs:
            raise HTTPException(status_code=422, detail=errs[0] if errs else "no bills found")
        return S.run_scan(recs, body.as_of or today_fn(), rates, errors=errs, default_agreed_days=body.default_agreed_days).to_dict()
