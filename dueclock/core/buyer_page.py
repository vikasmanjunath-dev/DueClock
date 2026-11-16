"""Buyer page: one server-rendered HTML page per invoice. No login, no JavaScript, no images, no external requests.

Budget: under 50 KB (the real page is a few KB). Everything dynamic is HTML-escaped. Legal wording follows
the Claims sheet: computed for CA review, never a threat or a promise of outcome.
"""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import date
from html import escape as e

from .i18n import LANGS, LANG_NAMES, norm_lang, reason_label, t
from typing import Dict, Optional
from urllib.parse import quote, urlencode

MAX_PAGE_BYTES = 50_000
PROMISE_MAX_DAYS = 60
DISPUTE_REASONS = ("Goods not received", "Quality issue", "Wrong amount", "Already paid", "Other")
_VPA = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$")

CSS = (
    "body{font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f7f9;color:#111}"
    "main{max-width:560px;margin:0 auto;padding:16px}h1{font-size:20px;margin:8px 0}h2{font-size:17px;margin:0 0 8px}"
    ".c{background:#fff;border:1px solid #d9dde3;border-radius:10px;padding:14px;margin:12px 0}"
    ".amt{font-size:30px;font-weight:700}.st{font-weight:600}.warn{border-color:#c77d00;background:#fff8e6}.ok{border-color:#2e7d32;background:#eef8ee}"
    "table{width:100%;border-collapse:collapse}td{padding:4px 0;vertical-align:top}td:first-child{color:#555;width:42%}"
    "a.b,button{display:block;width:100%;box-sizing:border-box;text-align:center;background:#0b5cad;color:#fff;border:0;border-radius:8px;"
    "padding:12px;font:inherit;font-weight:600;text-decoration:none;margin-top:8px}"
    "input,select,textarea{width:100%;box-sizing:border-box;padding:10px;font:inherit;border:1px solid #888;border-radius:6px;margin:4px 0 4px}"
    "label{font-weight:600}small,.s{color:#444;font-size:14px}footer{color:#444;font-size:13px;margin:16px 0}"
    ".lg{font-size:14px;color:#444;margin:4px 0}.lg a{color:#0b5cad}"
)
CSS_HASH = "sha256-" + base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()


def security_headers() -> Dict[str, str]:
    return {
        "Content-Security-Policy": f"default-src 'none'; style-src '{CSS_HASH}'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex, nofollow",
        "X-Frame-Options": "DENY",
    }


def inr(x: float) -> str:
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail, groups = whole[:-3], whole[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:]); head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"₹{whole}.{frac}"


def fmt_date(d: date, lang: str = "en") -> str:
    from .i18n import month_abbr
    return f"{d.day} {month_abbr(lang, d.month)} {d.year}"


def valid_vpa(vpa: Optional[str]) -> bool:
    return bool(vpa and _VPA.match(vpa))


def upi_link(vpa: str, payee: str, amount: float, note: str) -> Optional[str]:
    """UPI deep link to the seller's own account. We never hold or move money."""
    if not valid_vpa(vpa) or amount <= 0:
        return None
    return "upi://pay?" + urlencode({"pa": vpa, "pn": payee[:60], "am": f"{amount:.2f}", "cu": "INR", "tn": note[:50]}, quote_via=quote)


@dataclass
class PageData:
    token: str
    today: date
    seller_name: str
    invoice_number: str
    invoice_date: date
    amount: float
    outstanding: float
    due_date: date
    days_remaining: int
    overdue_days: int
    status: str                         # OPEN | PAID | DISPUTED
    statutory_ok: bool
    fy_label: str
    fy_end_year: int
    interest: Optional[float] = None
    upi_vpa: Optional[str] = None
    promised_date: Optional[date] = None
    scan_url: str = "/exposure-scan"
    privacy_url: str = "/privacy"
    grievance_email: str = "support@dueclock.example"
    notice: str = ""
    lang: str = "en"


def _shell(title: str, body: str, lang: str = "en") -> str:
    return (f'<!doctype html><html lang="{norm_lang(lang)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow"><title>{e(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>')


def render_notice_page(title: str, message: str, lang: str = "en") -> str:
    return _shell(title, f'<h1>{e(title)}</h1><div class="c">{e(message)}</div>', lang)


def lang_bar(token: str, current: str) -> str:
    links = []
    for code in LANGS:
        name = e(LANG_NAMES[code])
        links.append(f"<b>{name}</b>" if code == current else f'<a href="/p/{e(token)}?lang={code}">{name}</a>')
    return '<nav class="lg" aria-label="Language">' + " · ".join(links) + "</nav>"


def render_invoice_page(d: PageData) -> str:
    L = norm_lang(d.lang)
    q = f"?lang={L}"                                         # forms keep the chosen language through the redirect
    seller, inv = e(d.seller_name), e(d.invoice_number)
    title = t(L, "title", inv=d.invoice_number, seller=d.seller_name)
    out = [lang_bar(d.token, L), f"<h1>{e(title)}</h1>"]
    if d.notice in ("promise", "dispute"):
        out.append(f'<div class="c ok" role="status">{e(t(L, "n_" + d.notice))}</div>')

    if d.status == "PAID":
        out.append(f'<div class="c ok"><h2>{e(t(L, "settled_h"))}</h2>{e(t(L, "settled_b"))}</div>')
        return _shell(title, "".join(out) + _footer(d), L)

    due = fmt_date(d.due_date, L)
    if d.overdue_days > 0:
        timing = e(t(L, "overdue_1" if d.overdue_days == 1 else "overdue_n", due=due, n=d.overdue_days))
    elif d.days_remaining == 0:
        timing = e(t(L, "today", due=due))
    else:
        timing = e(t(L, "future_1" if d.days_remaining == 1 else "future_n", due=due, n=d.days_remaining))
    box = "c warn" if d.overdue_days > 0 else "c"
    out.append(f'<div class="{box}"><div class="amt">{e(inr(d.outstanding))}</div><div class="st">{timing}</div></div>')

    rows = [(t(L, "r_supplier"), seller), (t(L, "r_number"), inv), (t(L, "r_date"), e(fmt_date(d.invoice_date, L))), (t(L, "r_amount"), e(inr(d.amount)))]
    if d.outstanding < d.amount:
        rows.append((t(L, "r_balance"), e(inr(d.outstanding))))
    out.append('<div class="c"><table>' + "".join(f"<tr><td>{e(a)}</td><td>{b}</td></tr>" for a, b in rows) + "</table></div>")

    if d.statutory_ok:
        if d.overdue_days > 0:
            msg = e(t(L, "tax_over", year=d.fy_end_year, fy=d.fy_label))
        else:
            msg = e(t(L, "tax_future", due=due, fy=d.fy_label))
        extra = ""
        if d.interest is not None and d.interest > 0:
            extra = f"<br>{e(t(L, 'interest', amt=inr(d.interest)))}"
        out.append(f'<div class="c warn"><h2>{e(t(L, "tax_h"))}</h2>{msg}{extra}<br><small>{e(t(L, "tax_note"))}</small></div>')

    link = upi_link(d.upi_vpa or "", d.seller_name, d.outstanding, f"Inv {d.invoice_number}")
    if link:
        out.append(f'<div class="c"><h2>{e(t(L, "pay_h"))}</h2><a class="b" href="{e(link, quote=True)}">{e(t(L, "pay_btn", amt=inr(d.outstanding)))}</a>'
                   f'<p class="s">{e(t(L, "pay_note", seller=d.seller_name, vpa=d.upi_vpa or ""))}</p></div>')
    else:
        out.append(f'<div class="c"><h2>{e(t(L, "pay_h"))}</h2>{e(t(L, "pay_ask", seller=d.seller_name))}</div>')

    if d.status == "DISPUTED":
        out.append(f'<div class="c">{e(t(L, "disputed"))}</div>')
    else:
        pd = f'<p class="s">{e(t(L, "promise_told", date=fmt_date(d.promised_date, L)))}</p>' if d.promised_date else ""
        maxd = date.fromordinal(d.today.toordinal() + PROMISE_MAX_DAYS)
        out.append(f'<div class="c"><h2>{e(t(L, "promise_h"))}</h2>{pd}<form method="post" action="/p/{e(d.token)}/promise{q}">'
                   f'<label for="d">{e(t(L, "promise_label"))}</label><input id="d" name="date" type="date" required '
                   f'min="{(date.fromordinal(d.today.toordinal() + 1)).isoformat()}" max="{maxd.isoformat()}">'
                   f'<button type="submit">{e(t(L, "promise_btn"))}</button></form></div>')
        opts = "".join(f'<option value="{e(r, quote=True)}">{e(reason_label(L, r))}</option>' for r in DISPUTE_REASONS)
        out.append(f'<div class="c"><h2>{e(t(L, "dispute_h"))}</h2><form method="post" action="/p/{e(d.token)}/dispute{q}">'
                   f'<label for="r">{e(t(L, "dispute_q"))}</label><select id="r" name="reason" required>{opts}</select>'
                   f'<label for="n">{e(t(L, "dispute_notes"))}</label><textarea id="n" name="note" rows="3" maxlength="500"></textarea>'
                   f'<button type="submit">{e(t(L, "dispute_btn"))}</button></form></div>')

    out.append(f'<div class="c"><h2>{e(t(L, "scan_h"))}</h2>{e(t(L, "scan_b"))}'
               f'<a class="b" href="{e(d.scan_url, quote=True)}">{e(t(L, "scan_btn"))}</a></div>')
    return _shell(title, "".join(out) + _footer(d), L)


def _footer(d: PageData) -> str:
    L = norm_lang(d.lang)
    privacy = f'<a href="{e(d.privacy_url, quote=True)}">{e(t(L, "privacy"))}</a>'
    text = e(t(L, "footer", seller=d.seller_name, privacy="\x00", email=d.grievance_email)).replace("\x00", privacy)
    return f"<footer>{text}</footer>"
