"""Owner screen pages: pure rendering (data in, HTML out). Server-rendered, no JavaScript, one inline stylesheet.

Everything dynamic is HTML-escaped. Every state-changing form carries a CSRF token.
"""
from __future__ import annotations

import base64
import hashlib
from datetime import date
from html import escape as e
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .buyer_page import fmt_date, inr

CSS = (
    "*{box-sizing:border-box}body{font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f4f6f8;color:#111}"
    "header{background:#0b3a6f;color:#fff;padding:10px 16px;display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center}"
    "header b{font-size:17px;margin-right:8px}header a{color:#dbe9ff;text-decoration:none;padding:4px 2px}header a.on{color:#fff;border-bottom:2px solid #fff}"
    "header form{margin-left:auto}main{max-width:1000px;margin:0 auto;padding:14px}h1{font-size:21px;margin:6px 0 10px}h2{font-size:17px;margin:0 0 8px}"
    ".c{background:#fff;border:1px solid #d9dde3;border-radius:10px;padding:12px;margin:10px 0;overflow-x:auto}"
    ".tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}.t{background:#fff;border:1px solid #d9dde3;border-radius:10px;padding:10px}"
    ".t b{display:block;font-size:20px}.t span{color:#555;font-size:13px}.t.bad{border-color:#c0392b;background:#fdf1ef}.t.warn{border-color:#c77d00;background:#fff8e6}"
    "table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #e8ebee;vertical-align:top}th{font-size:13px;color:#555}"
    "td.n,th.n{text-align:right;white-space:nowrap}.badge{display:inline-block;font-size:12px;padding:1px 7px;border-radius:10px;background:#e8ebee;margin:1px 2px 1px 0}"
    ".badge.bad{background:#f8d7d3}.badge.warn{background:#ffeab8}.badge.ok{background:#d5efd8}"
    ".btn,button{display:inline-block;background:#0b5cad;color:#fff;border:0;border-radius:7px;padding:7px 12px;font:inherit;text-decoration:none;cursor:pointer}"
    "button.alt,.btn.alt{background:#fff;color:#0b5cad;border:1px solid #0b5cad}button.red{background:#b3261e}"
    "input,select,textarea{font:inherit;padding:7px;border:1px solid #888;border-radius:6px;max-width:100%}textarea{width:100%}label{font-weight:600;display:block;margin:6px 0 2px}"
    ".flash{background:#e7f5e8;border:1px solid #2e7d32;border-radius:8px;padding:8px 12px;margin:10px 0}.err{background:#fdf1ef;border-color:#c0392b}"
    ".fe{color:#b00020;font-size:13px;font-weight:600}.pv{margin:4px 0}.pv.warn{color:#8a5a00}.pv.bad{color:#b00020}.pv.ok{color:#1b5e20}.banner{background:#fff8e6;border:1px solid #c77d00;border-radius:8px;padding:10px;margin:10px 0}"
    ".muted{color:#555;font-size:13px}.row{display:flex;flex-wrap:wrap;gap:8px;align-items:end}.filters a{margin-right:10px}.filters a.on{font-weight:700;text-decoration:none;color:#111}"
    "code{background:#eef1f4;padding:1px 4px;border-radius:4px;word-break:break-all}dl{display:grid;grid-template-columns:150px 1fr;gap:4px 10px;margin:0}dt{color:#555}dd{margin:0}"
)
CSS_HASH = "sha256-" + base64.b64encode(hashlib.sha256(CSS.encode()).digest()).decode()


def owner_headers() -> Dict[str, str]:
    return {
        "Content-Security-Policy": f"default-src 'none'; style-src '{CSS_HASH}'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "X-Robots-Tag": "noindex",
    }


FLASH = {
    "confirmed": "Match confirmed. Reminders for that invoice have stopped.", "rejected": "Suggestion rejected.", "undone": "Match undone.",
    "manual": "Payment assigned.", "ignored": "Credit ignored.", "imported": "Statement imported.", "saved": "Saved.", "paused": "Reminders paused.",
    "resumed": "Reminders resumed.", "link": "Buyer link created.", "revoked": "Buyer links revoked.", "resolved": "Dispute marked resolved.",
    "optin": "Consent recorded.", "optout": "Consent withdrawn. No more reminders to this buyer.", "agreement": "Payment terms updated.",
}
FLASH_ERR = {"conflict": "That action could not be done: it conflicts with the current state.", "invalid": "That input was not valid.", "consent": "Please tick the consent confirmation.", "fields": "Please fix the highlighted fields."}
STEP_NAMES = {"S1": "Heads-up", "S2": "Due-day reminder", "S3": "3 days late", "S4": "10 days late", "S5": "30 days late", "P1": "Promise-date follow-up", "S6": "Promise thanks", "S7": "Payment received"}

NAV = (("/app", "Collections", "dash"), ("/app/payments", "Payments", "pay"), ("/app/settings", "Settings", "set"))


def layout(title: str, body: str, *, active: str = "", csrf: str = "", seller: str = "", flash: str = "", err: str = "") -> str:
    nav = ""
    if csrf:
        links = "".join(f'<a href="{h}"{" class=on" if k == active else ""}>{t}</a>' for h, t, k in NAV)
        nav = (f'<header><b>DueClock</b>{links}<form method="post" action="/app/logout"><input type="hidden" name="csrf" value="{e(csrf)}">'
               f'<button class="alt" type="submit">Sign out{(" (" + e(seller) + ")") if seller else ""}</button></form></header>')
    msgs = ""
    if flash in FLASH:
        msgs += f'<div class="flash" role="status">{e(FLASH[flash])}</div>'
    if err in FLASH_ERR:
        msgs += f'<div class="flash err" role="alert">{e(FLASH_ERR[err])}</div>'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>{e(title)} · DueClock</title><style>{CSS}</style></head><body>{nav}<main>{msgs}{body}</main></body></html>')


def login_page(error: bool = False, limited: bool = False) -> str:
    msg = ""
    if limited:
        msg = '<div class="flash err" role="alert">Too many attempts. Please wait a few minutes and try again.</div>'
    elif error:
        msg = '<div class="flash err" role="alert">That access key was not recognised.</div>'
    body = (f'<h1>Sign in</h1>{msg}<div class="c"><form method="post" action="/app/login"><label for="k">Access key</label>'
            f'<input id="k" name="key" type="password" autocomplete="current-password" required style="width:100%"><p class="muted">'
            f'The key we gave you for your company. It is never shown again after setup.</p><button type="submit">Sign in</button></form></div>')
    return layout("Sign in", body)


def _badge(text: str, kind: str = "") -> str:
    return f'<span class="badge {kind}">{e(text)}</span>'


def badges(items: Sequence[Tuple[str, str]]) -> str:
    return "".join(_badge(t, k) for t, k in items)


def _csrf(csrf: str) -> str:
    return f'<input type="hidden" name="csrf" value="{e(csrf)}">'


def dashboard_page(d: Dict[str, Any], csrf: str, seller: str, flash: str = "", err: str = "") -> str:
    d = dict(d)
    banner = ('<div class="banner" role="status"><b>Finish your business details.</b> Until your category and activity are set, DueClock cannot tell whether the '
              'statutory payment clock applies, so invoices show no interest or 43B(h) warning. <a href="/app/settings#business">Open Settings</a></div>') if d.get("profile_incomplete") else ""
    t = d["tiles"]
    tile = lambda label, value, kind="": f'<div class="t {kind}"><b>{e(value)}</b><span>{e(label)}</span></div>'
    tiles = "".join([
        tile("Outstanding", inr(t["outstanding"])), tile(f"Overdue ({t['overdue_count']})", inr(t["overdue_amount"]), "bad" if t["overdue_count"] else ""),
        tile("Due in 7 days", inr(t["due7_amount"]), "warn" if t["due7_amount"] else ""), tile("Interest accruing", inr(t["interest"])),
        tile("43B(h) exposure (bills)", str(t["exposure_count"]), "warn" if t["exposure_count"] else ""),
    ])
    notes = ""
    if d["paused"]:
        notes += '<div class="flash err">Reminders are paused for your whole company. <a href="/app/settings">Resume in Settings</a>.</div>'
    ls = d["last_sync"]
    if ls is None:
        notes += '<div class="flash err">No Tally sync has arrived yet. Check that the connector is installed and running.</div>'
    elif d["stale"]:
        notes += f'<div class="flash err">The last Tally sync was {e(ls["finished_at"][:16].replace("T", " "))} UTC. The figures below may be out of date.</div>'
    if d["credits_waiting"]:
        notes += f'<div class="flash">{d["credits_waiting"]} bank credit(s) are waiting to be matched. <a href="/app/payments">Review payments</a>.</div>'
    f = d["filter"]
    cnt = d["counts"]
    filters = " ".join(f'<a href="/app?filter={k}"{" class=on" if k == f else ""}>{label} ({cnt[k]})</a>' for k, label in
                       (("all", "All open"), ("overdue", "Overdue"), ("due7", "Due in 7 days"), ("disputed", "Disputed"), ("attention", "Needs attention")))
    rows = []
    for r in d["rows"]:
        status = (f"{r['overdue_days']} day{'s' if r['overdue_days'] != 1 else ''} overdue" if r["overdue_days"] > 0 else
                  ("due today" if r["days_remaining"] == 0 else f"due in {r['days_remaining']} day{'s' if r['days_remaining'] != 1 else ''}"))
        rows.append(f'<tr><td><a href="/app/invoices/{e(r["id"])}">{e(r["number"])}</a><br><span class="muted">{e(fmt_date(r["invoice_date"]))}</span></td>'
                    f'<td>{e(r["buyer"])}</td><td class="n">{e(inr(r["outstanding"]))}</td><td>{e(fmt_date(r["due_date"]))}<br><span class="muted">{e(status)}</span></td>'
                    f'<td>{badges(r["badges"])}</td><td class="muted">{e(r["next_step"])}</td></tr>')
    table = ('<div class="c"><table><thead><tr><th>Invoice</th><th>Buyer</th><th class="n">Outstanding</th><th>Due</th><th>Flags</th><th>Next reminder</th></tr></thead><tbody>'
             + ("".join(rows) or '<tr><td colspan="6" class="muted">Nothing here.</td></tr>') + "</tbody></table>"
             + (f'<p class="muted">Showing the first {len(rows)} of {d["shown_of"]}.</p>' if d["shown_of"] > len(rows) else "") + "</div>")
    rem = d["reminders"]
    foot = (f'<p class="muted">As of {e(fmt_date(d["as_of"]))}. Reminders sent: {rem["accepted"]}'
            + (f', opened: {round(rem["open_rate"] * 100)}%' if rem["open_rate"] is not None else "") + '. Computed for your CA to review; not legal advice.</p>')
    body = f'<h1>Collections</h1>{banner}{notes}<div class="tiles">{tiles}</div><p class="filters">{filters}</p>{table}{foot}'
    return layout("Collections", body, active="dash", csrf=csrf, seller=seller, flash=flash, err=err)


def _yn_form(action: str, csrf: str, name: str, yes: str, no: str, current: bool, label: str) -> str:
    return (f'<form method="post" action="{e(action)}" class="row">{_csrf(csrf)}<span>{e(label)}: <b>{"Yes" if current else "No"}</b></span>'
            f'<button class="alt" name="{name}" value="{"no" if current else "yes"}" type="submit">{e(no if current else yes)}</button></form>')


def invoice_page(d: Dict[str, Any], csrf: str, seller: str, flash: str = "", err: str = "") -> str:
    v, c = d["view"], d["view"]["clock"]
    iid = e(v["id"])
    status = (f'{c["overdue_days"]} day{"s" if c["overdue_days"] != 1 else ""} overdue' if c["overdue_days"] > 0 else
              ("due today" if c["days_remaining"] == 0 else f'due in {c["days_remaining"]} day{"s" if c["days_remaining"] != 1 else ""}'))
    facts = [("Buyer", e(v["buyer_name"])), ("Invoice date", e(fmt_date(date.fromisoformat(v["invoice_date"])))), ("Invoice amount", e(inr(v["amount"]))),
             ("Outstanding", e(inr(v["outstanding"])) + (f' <span class="muted">(Tally shows {e(inr(v["tally_outstanding"]))}; {e(inr(v["unposted_payments"]))} matched to a bank credit, not yet in Tally)</span>' if v["unposted_payments"] else "")),
             ("Due date", f'{e(fmt_date(date.fromisoformat(c["due_date"])))} <span class="muted">({e(status)})</span>'),
             ("Statutory rule", e(f'{c["coverage"].replace("_", " ").title()}; {c["effective_days"]} days' + (f' (cap {c["statutory_cap_days"]})' if c["statutory_cap_days"] else ""))),
             ("Interest accrued", e(inr(c["interest"])) + f' <span class="muted">at {c["annual_interest_rate_pct"]:.2f}% a year</span>' if c["covered"] else "Not applicable"),
             ("Status", badges(d["badges"]) or "Open")]
    dl = "<dl>" + "".join(f"<dt>{k}</dt><dd>{val}</dd>" for k, val in facts) + "</dl>"
    flags = ""
    if c["review_flags"]:
        flags = '<p class="muted">Review flags: ' + ", ".join(e(x) for x in c["review_flags"]) + "</p>"
    trace = "<ul>" + "".join(f"<li>{e(t)}</li>" for t in c["trace"]) + "</ul>"
    summary = f'<div class="c"><h2>Invoice {e(v["invoice_number"])}</h2>{dl}{flags}<details><summary class="muted">How this was worked out</summary>{trace}</details></div>'

    consent = ""
    if d["opted_in"]:
        consent = _yn_form(f"/app/invoices/{iid}/optin", csrf, "opt", "Record consent", "Withdraw consent", True, "Buyer has agreed to WhatsApp reminders")
    else:
        consent = (f'<form method="post" action="/app/invoices/{iid}/optin">{_csrf(csrf)}<label><input type="checkbox" name="confirm" value="on"> '
                   f'I confirm this buyer has agreed to receive payment reminders on WhatsApp.</label><input type="hidden" name="opt" value="yes">'
                   f'<p><button type="submit">Record consent</button></p></form>')
    link = ""
    if d["link_url"]:
        link = f'<p>Buyer link (expires {e(d["link_expires"][:10])}; opened {d["link_views"]} time{"s" if d["link_views"] != 1 else ""}):<br><code>{e(d["link_url"])}</code></p>'
    controls = (
        f'<div class="c"><h2>Reminders and terms</h2>'
        + (_yn_form(f"/app/invoices/{iid}/agreement", csrf, "value", "", "Mark as not agreed", True, "Written agreement with this buyer")
           if d["agreement"] else
           f'<form method="post" action="/app/invoices/{iid}/agreement" class="row">{_csrf(csrf)}<div><label for="dy">Written payment agreement (days agreed, up to 45)</label>'
           f'<input id="dy" name="days" type="number" min="1" max="45" value="45" size="4"></div><button class="alt" name="value" value="yes" type="submit">Record agreement</button></form>')
        + "<hr>" + consent + "<hr>"
        + _yn_form(f"/app/invoices/{iid}/pause", csrf, "value", "Pause reminders", "Resume reminders", d["paused"], "Reminders paused for this invoice")
        + "<hr>" + link
        + f'<form method="post" action="/app/invoices/{iid}/link" class="row">{_csrf(csrf)}<button class="alt" type="submit">Create buyer link</button></form>'
        + (f'<form method="post" action="/app/invoices/{iid}/link/revoke" class="row" style="margin-top:6px">{_csrf(csrf)}<button class="alt red" type="submit">Revoke links</button></form>' if d["link_url"] else "")
        + (f'<hr><form method="post" action="/app/invoices/{iid}/dispute/resolve" class="row">{_csrf(csrf)}<span>The buyer disputed this invoice.</span><button type="submit">Mark dispute resolved</button></form>' if v["status"] == "DISPUTED" else "")
        + f'<p class="muted">Next reminder: {e(d["next_step"] or "none")}</p></div>')
    tl = "".join(f'<tr><td class="muted" style="white-space:nowrap">{e(t["at"][:16].replace("T", " "))}</td><td>{e(t["text"])}</td></tr>' for t in d["timeline"])
    timeline = f'<div class="c"><h2>History</h2><table><tbody>{tl or "<tr><td class=muted>Nothing yet.</td></tr>"}</tbody></table></div>'
    body = f'<p><a href="/app">&larr; Collections</a></p><h1>{e(v["buyer_name"])}</h1>{summary}{controls}{timeline}'
    return layout(f"Invoice {v['invoice_number']}", body, active="dash", csrf=csrf, seller=seller, flash=flash, err=err)


def payments_page(d: Dict[str, Any], csrf: str, seller: str, flash: str = "", err: str = "") -> str:
    s = d["summary"]
    cards = []
    for cr in d["credits"]:
        head = (f'<b>{e(inr(cr["amount"]))}</b> on {e(fmt_date(date.fromisoformat(cr["date"])))} '
                f'{_badge(cr["status"], "ok" if cr["status"] == "matched" else ("warn" if cr["status"] in ("suggested", "partial") else ""))}'
                f'<br><span class="muted">{e(cr["narration"] or "")}</span>')
        ms = ""
        for m in cr["matches"]:
            allocs = "; ".join(f'{e(a["invoice_number"] or "")} {e(inr(a["amount"]))}' for a in m["allocations"])
            reasons = ", ".join(e(r.replace("_", " ").lower()) for r in m["reasons"])
            if m["status"] == "suggested":
                act = (f'<form method="post" action="/app/matches/{e(m["id"])}/confirm" class="row" style="display:inline">{_csrf(csrf)}<button type="submit">Confirm</button></form> '
                       f'<form method="post" action="/app/matches/{e(m["id"])}/reject" style="display:inline">{_csrf(csrf)}<button class="alt" type="submit">Reject</button></form>')
            elif m["status"] == "confirmed":
                act = (f'{_badge("confirmed", "ok")} <form method="post" action="/app/matches/{e(m["id"])}/unconfirm" style="display:inline">{_csrf(csrf)}'
                       f'<button class="alt" type="submit">Undo</button></form>')
            else:
                act = _badge(m["status"])
            ms += f'<p>{allocs} <span class="muted">(confidence {round(m["score"] * 100)}%: {reasons})</span><br>{act}</p>'
        manual = ""
        if cr["status"] in ("unmatched", "suggested", "partial") and d["open_invoices"]:
            opts = "".join(f'<option value="{e(i["id"])}">{e(i["invoice_number"])} · {e(i["buyer_name"])} · {e(inr(i["effective_paise"] / 100))}</option>' for i in d["open_invoices"])
            manual = (f'<form method="post" action="/app/credits/{e(cr["id"])}/manual" class="row">{_csrf(csrf)}<div><label>Assign to invoice</label><select name="invoice_id">{opts}</select></div>'
                      f'<div><label>Amount</label><input name="amount" value="{cr["amount"] - cr["matched"]:.2f}" size="10"></div><button class="alt" type="submit">Assign</button></form>'
                      f'<form method="post" action="/app/credits/{e(cr["id"])}/ignore" style="margin-top:6px">{_csrf(csrf)}<button class="alt" type="submit">Ignore this credit</button></form>')
        cards.append(f'<div class="c">{head}{ms}{manual}</div>')
    imp = (f'<div class="c"><h2>Import a bank statement</h2><form method="post" action="/app/credits/import">{_csrf(csrf)}<label for="t">Paste the CSV (with the header row)</label>'
           f'<textarea id="t" name="text" rows="5" required></textarea><p><button type="submit">Import and suggest matches</button></p></form></div>')
    cr_s = s["credits"]
    sumtxt = ", ".join(f'{k}: {v["count"]}' for k, v in sorted(cr_s.items())) or "no credits yet"
    mn = s["matched_not_yet_in_tally"]
    body = (f'<h1>Payments</h1><p class="muted">Credits: {e(sumtxt)}. Matched but not yet posted in Tally: {mn["invoices"]} invoice(s), {e(inr(mn["amount"]))}.</p>'
            f'{imp}{"".join(cards) or "<p class=muted>No bank credits imported yet.</p>"}')
    return layout("Payments", body, active="pay", csrf=csrf, seller=seller, flash=flash, err=err)


def _field(name: str, label: str, value: Any, errors: Dict[str, str], *, kind: str = "text", hint: str = "", options: Sequence[Tuple[str, str]] = (), maxlength: int = 120) -> str:
    v = "" if value is None else str(value)
    msg = f'<div class="fe" role="alert">{e(errors[name])}</div>' if name in errors else ""
    if options:
        opts = '<option value="">Choose...</option>' + "".join(f'<option value="{e(k)}"{" selected" if k == v.upper() else ""}>{e(t)}</option>' for k, t in options)
        ctl = f'<select id="f_{name}" name="{name}">{opts}</select>'
    else:
        ctl = f'<input id="f_{name}" name="{name}" type="{kind}" value="{e(v, quote=True)}" maxlength="{maxlength}"' + (' aria-invalid="true"' if name in errors else "") + ">"
    return f'<label for="f_{name}">{e(label)}</label>{ctl}{msg}' + (f'<p class="muted">{e(hint)}</p>' if hint and name not in errors else "")


def business_card(d: Dict[str, Any], csrf: str) -> str:
    sv, er = d.get("profile") or {}, d.get("profile_errors") or {}
    cat = (("MICRO", "Micro"), ("SMALL", "Small"), ("MEDIUM", "Medium"), ("LARGE", "Large"))
    act = (("MANUFACTURING", "Manufacturing"), ("SERVICES", "Services"), ("TRADING", "Trading"))
    prev = "".join(f'<li class="pv {e(k)}">{e(t)}</li>' for k, t in d.get("profile_preview", []))
    return (f'<div class="c" id="business"><h2>Your business</h2><p class="muted">This decides whether the statutory payment clock applies to your invoices. '
            f'Take the category and activity from your Udyam certificate.</p>'
            f'<form method="post" action="/app/settings/business">{_csrf(csrf)}'
            + _field("business_name", "Business name (shown to buyers)", sv.get("business_name"), er)
            + _field("category", "Category (Udyam)", sv.get("category"), er, options=cat)
            + _field("activity", "Activity", sv.get("activity"), er, options=act, hint="Traders are outside the MSMED payment rules.")
            + _field("udyam_number", "Udyam registration number", sv.get("udyam_number"), er, hint="UDYAM-KA-03-0012345")
            + _field("udyam_registration_date", "Udyam registration date", sv.get("udyam_registration_date"), er, kind="date", hint="From the certificate. Supplies before this date are flagged for review.")
            + _field("gstin", "GSTIN (optional)", sv.get("gstin"), er, maxlength=15)
            + _field("upi_vpa", "Your UPI ID (for the Pay button)", sv.get("upi_vpa"), er, hint="Buyers pay this account directly; DueClock never holds money.")
            + _field("phone", "Phone (optional)", sv.get("phone"), er, kind="tel", maxlength=20)
            + _field("email", "Email (optional)", sv.get("email"), er, kind="email")
            + f'<button type="submit">Save business details</button></form>'
            + (f'<h3>What this means</h3><ul>{prev}</ul>' if prev else "") + '</div>')


def settings_page(d: Dict[str, Any], csrf: str, seller: str, flash: str = "", err: str = "") -> str:
    rem = d["reminders"]
    paused = d["paused"]
    toggle = (f'<form method="post" action="/app/settings/reminders" class="row">{_csrf(csrf)}<span>Reminders are <b>{"PAUSED" if paused else "on"}</b> for the whole company.</span>'
              f'<button class="{"" if paused else "red"}" name="value" value="{"resume" if paused else "pause"}" type="submit">{"Resume reminders" if paused else "Pause all reminders"}</button></form>')
    prev = ""
    if d["preview"]["decisions"]:
        prev = "<ul>" + "".join(f'<li>{e(x["buyer"])} · {e(x["invoice"])} · {e(STEP_NAMES.get(x["step"].split(":")[0], x["step"]))}</li>' for x in d["preview"]["decisions"][:20]) + "</ul>"
    else:
        prev = '<p class="muted">Nothing is due to send right now (it depends on the time of day, consent and each invoice\'s schedule).</p>'
    sy = "".join(f'<tr><td>{e((s_["finished_at"] or "")[:16].replace("T", " "))}</td><td>{e(s_["status"])}</td><td class="n">{s_["records_received"]}</td><td class="n">{s_["errors"]}</td><td>{e(s_["connector_version"] or "")}</td><td>{e(s_["tally_version"] or "")}</td></tr>' for s_ in d["syncs"])
    body = (f'<h1>Settings</h1>{business_card(d, csrf)}<div class="c"><h2>Reminders</h2>{toggle}'
            f'<p class="muted">WhatsApp sender: {"configured" if d["sender"] else "not configured (reminders cannot be sent)"}. Languages: {e(", ".join(d["languages"]))}. '
            f'Sent: {rem["accepted"]}{(", opened: " + str(round(rem["open_rate"] * 100)) + "%") if rem["open_rate"] is not None else ""}.</p>'
            f'<h2>Would send now</h2>{prev}</div>'
            f'<div class="c"><h2>Tally syncs</h2><table><thead><tr><th>When (UTC)</th><th>Status</th><th class="n">Records</th><th class="n">Errors</th><th>Connector</th><th>Tally</th></tr></thead><tbody>'
            f'{sy or "<tr><td colspan=6 class=muted>No syncs yet.</td></tr>"}</tbody></table></div>')
    return layout("Settings", body, active="set", csrf=csrf, seller=seller, flash=flash, err=err)


def not_found_page() -> str:
    return layout("Not found", '<h1>Not found</h1><p><a href="/app">Back to Collections</a></p>')
