"""`tally-check`: find out, on a real TallyPrime, which of our read-only requests work and whether the parser understands the reply.

Run once on the first install (and after a Tally upgrade). Every request goes through ``TallyClient.post`` (read-only guard).
Raw replies are saved REDACTED by default (names, references and free text replaced by stable pseudonyms; dates, amounts and
structure kept) so a capture can be shared and used as a parser test fixture. The working variants can be saved to the config.
"""
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import CONNECTOR_VERSION
from .tally_client import BILLS_VARIANTS, COMPANY_VARIANTS, TallyClient, TallyError, bills_receivable_request, companies_request, custom_request

SAFE_TAGS = {"BILLDATE", "DATE", "BILLCL", "BILLDUE", "BILLOVERDUE", "BILLOPENING", "BILLOPENGING", "BILLCREDITPERIOD", "AMOUNT", "CLOSINGBALANCE",
             "OPENINGBALANCE", "PENDINGAMOUNT", "VERSION", "TALLYREQUEST", "TYPE", "ID", "SVEXPORTFORMAT", "EXPLODEFLAG", "STATUS", "LINEERROR"}
STRUCTURAL = {"ENVELOPE", "HEADER", "BODY", "DATA", "DESC", "COLLECTION", "IMPORTDATA", "REQUESTDESC", "REQUESTDATA"}
PSEUDO_PREFIX = {"BILLREF": "REF", "BILLNAME": "REF", "VOUCHERNUMBER": "REF", "REFERENCE": "REF", "BILLPARTY": "PARTY", "PARTYLEDGERNAME": "PARTY", "PARTYNAME": "PARTY",
                 "COMPANYNAME": "COMPANY", "SVCURRENTCOMPANY": "COMPANY"}


class _Pseudo:
    def __init__(self):
        self.maps: Dict[str, Dict[str, str]] = {}

    def __call__(self, tag: str, text: str) -> str:
        prefix = PSEUDO_PREFIX.get(tag.upper(), "TXT")
        m = self.maps.setdefault(prefix, {})
        return m.setdefault(text, f"{prefix}-{len(m) + 1}")


_NODE = re.compile(r"<([A-Za-z0-9_.:\-]+)([^<>]*)>([^<>]*)</\1>")


def redact_xml(text: str) -> str:
    """Keep structure, dates, amounts and keywords; replace every other text value (and every attribute value) with a stable pseudonym."""
    ps = _Pseudo()
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # Invalid XML is itself a finding; keep its shape but never its text.
        return _NODE.sub(lambda m: f"<{m.group(1)}>{m.group(3) if m.group(1).upper() in SAFE_TAGS else ps(m.group(1), m.group(3).strip()) if m.group(3).strip() else ''}</{m.group(1)}>", text)
    for el in root.iter():
        el.attrib = {k: "x" for k in el.attrib}
        if el.text and el.text.strip() and el.tag.upper() not in SAFE_TAGS:
            el.text = ps(el.tag, el.text.strip())
        elif el.text and not el.text.strip():
            el.text = None
        if el.tail and not el.tail.strip():
            el.tail = None
    return ET.tostring(root, encoding="unicode")


def _stats(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(records)
    keys = Counter((r["invoice_number"].strip().lower(), " ".join(r["buyer_name"].lower().split())) for r in records)
    return {
        "records": n, "buyers": len({r["buyer_name"] for r in records}),
        "with_credit_days": sum(1 for r in records if r.get("agreed_credit_days", 0) > 0),
        "amount_not_in_report": sum(1 for r in records if r.get("amount_known") is False),
        "zero_pending": sum(1 for r in records if not r.get("pending_amount")),
        "duplicate_keys": sum(1 for c in keys.values() if c > 1),
        "oldest_invoice": min((r["invoice_date"] for r in records), default=None),
        "newest_invoice": max((r["invoice_date"] for r in records), default=None),
        "total_pending": round(sum(r.get("pending_amount") or 0 for r in records), 2),
    }


def _tag_census(xml: str, top: int = 20) -> List[str]:
    try:
        return [t for t, _ in Counter(e.tag for e in ET.fromstring(xml).iter()).most_common(top)]
    except ET.ParseError:
        return []


def _finding(level: str, code: str, message: str) -> Dict[str, str]:
    return {"level": level, "code": code, "message": message}


def run_check(client: TallyClient, *, company: Optional[str] = None, out_dir: Optional[str] = None, redact: bool = True, bills_template: Optional[str] = None) -> Dict[str, Any]:
    """Never raises for Tally problems: every failure becomes a finding."""
    rep: Dict[str, Any] = {"when": datetime.now().isoformat(timespec="seconds"), "connector_version": CONNECTOR_VERSION, "company": company, "findings": [],
                           "tally": {}, "companies": {"attempts": [], "chosen": None, "names": 0}, "bills": {"attempts": [], "chosen": None}, "captures": [], "profile": {}}
    F = rep["findings"]
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    def capture(name: str, raw: str) -> None:
        if not out_dir:
            return
        path = os.path.join(out_dir, name + ".xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(redact_xml(raw) if redact else raw)
        rep["captures"].append(path)

    pr = client.probe()
    rep["tally"] = {"running": pr.running, "product": pr.product, "message": pr.message}
    if not pr.running:
        F.append(_finding("fail", "TALLY_UNREACHABLE", "Tally did not answer on this address/port. Open the company in Tally and enable the HTTP/XML server (port 9000), then run again."))
        rep["verdict"] = "FAIL"
        return rep

    # --- companies
    for v in COMPANY_VARIANTS:
        a: Dict[str, Any] = {"variant": v}
        t0 = time.time()
        try:
            raw = client.post(companies_request(v))
            capture(f"companies_{v}", raw)
            ET.fromstring(raw)
            a["xml_valid"] = True
        except TallyError as e:
            a.update(ok=False, error=str(e))
            rep["companies"]["attempts"].append(a)
            continue
        except ET.ParseError:
            a["xml_valid"] = False
        a["ms"] = int((time.time() - t0) * 1000)
        names = _company_names(raw)
        a["names"], a["ok"] = len(names), bool(names)
        rep["companies"]["attempts"].append(a)
        if names and rep["companies"]["chosen"] is None:
            rep["companies"]["chosen"], rep["companies"]["names"] = v, len(names)
    if rep["companies"]["chosen"]:
        rep["profile"]["companies"] = rep["companies"]["chosen"]
        if rep["companies"]["names"] > 1 and not company:
            F.append(_finding("warn", "MULTIPLE_COMPANIES", f"{rep['companies']['names']} companies are loaded; set 'companies' in the config or pass --company, otherwise only the open company is read."))
    else:
        F.append(_finding("warn", "COMPANY_LIST_FAILED", "No company-list request worked. The connector can still read the company that is open in Tally."))

    # --- bills receivable
    attempts = [(v, bills_receivable_request(company, v)) for v in BILLS_VARIANTS]
    if bills_template:
        attempts.append(("custom_template", custom_request(bills_template, company)))
    best_raw: Optional[str] = None
    for v, req in attempts:
        a = {"variant": v}
        t0 = time.time()
        try:
            raw = client.post(req)
        except Exception as e:                                           # includes ReadOnlyViolation for a bad custom template
            a.update(ok=False, error=f"{type(e).__name__}: {e}"[:200])
            rep["bills"]["attempts"].append(a)
            continue
        a["ms"], a["bytes"] = int((time.time() - t0) * 1000), len(raw)
        capture(f"bills_{v}", raw)
        pr_ = _parse(raw)
        a.update(pr_)
        rep["bills"]["attempts"].append(a)
        if a.get("records", 0) > 0 and rep["bills"]["chosen"] is None:
            rep["bills"]["chosen"], best_raw = v, raw
    chosen = rep["bills"]["chosen"]
    if chosen:
        rep["profile"]["bills"] = chosen if chosen in BILLS_VARIANTS else rep["profile"].get("bills", "data_explode")
        ca = next(a for a in rep["bills"]["attempts"] if a["variant"] == chosen)
        st = ca["stats"]
        F.append(_finding("ok", "BILLS_PARSED", f"{st['records']} open bills for {st['buyers']} buyers read with variant '{chosen}'."))
        if ca["errors_total"]:
            level = "warn" if ca["errors_total"] > max(1, st["records"] // 5) else "info"
            F.append(_finding(level, "PARSE_ERRORS", f"{ca['errors_total']} rows could not be read (first: {ca['errors'][:2]})."))
        if st["amount_not_in_report"]:
            F.append(_finding("info", "AMOUNT_NOT_IN_REPORT", "The report gives pending balances, not original invoice amounts; pending is used for both."))
        if st["with_credit_days"]:
            F.append(_finding("info", "CREDIT_DAYS_FROM_DUE_DATE", "Credit days are inferred as (Tally due date - bill date). That is Tally's own credit term, not proof of a written agreement; owners must confirm agreements."))
        if st["duplicate_keys"]:
            F.append(_finding("warn", "DUPLICATE_BILLS", f"{st['duplicate_keys']} bills share invoice number, buyer and date; they would be treated as one."))
        if st["zero_pending"]:
            F.append(_finding("info", "ZERO_PENDING", f"{st['zero_pending']} bills show a zero pending amount."))
        if best_raw and best_raw.count("\x00"):
            F.append(_finding("info", "CONTROL_CHARACTERS", "Tally sent control characters; they were stripped."))
    else:
        valid = [a for a in rep["bills"]["attempts"] if a.get("xml_valid")]
        def informative(a):
            return a.get("shape") == "unknown" and [t for t in a.get("tags", []) if t.upper() not in STRUCTURAL]
        if valid and any(informative(a) for a in valid):
            tags = next(informative(a) for a in valid if informative(a))
            F.append(_finding("fail", "PARSER_SHAPE_UNKNOWN", f"Tally answered, but the reply is not a shape the parser knows. Tags seen: {', '.join(tags[:15])}. Send the redacted capture; the parser or the request needs adjusting."))
        elif valid:
            F.append(_finding("warn", "ZERO_BILLS", "Tally answered with valid XML but no bills were found: the open company may have no outstanding receivables, or the report needs a different request."))
        else:
            F.append(_finding("fail", "BILLS_REQUEST_FAILED", "No bills request produced readable XML. See 'attempts' for each error and the captured replies."))
    rep["verdict"] = "FAIL" if any(f["level"] == "fail" for f in F) else ("PARTIAL" if any(f["level"] == "warn" for f in F) else "PASS")
    if out_dir:
        with open(os.path.join(out_dir, "tally_check_report.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2, default=str)
    return rep


def _company_names(xml: str) -> List[str]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    for tag in ("COMPANYNAME", "NAME"):
        names = [e.text.strip() for e in root.iter(tag) if e.text and e.text.strip()]
        if names:
            return list(dict.fromkeys(names))
    return []


def _parse(raw: str) -> Dict[str, Any]:
    from dueclock.core.tally_parse import parse_tally_response
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return {"xml_valid": False, "records": 0, "errors_total": 1, "errors": [f"invalid XML: {e}"], "shape": "invalid"}
    shape = "flat" if root.find(".//BILLFIXED") is not None else ("nested" if any(root.find(f".//{t}") is not None for t in ("BILLOUTSTANDING", "BILL", "VOUCHER")) else "unknown")
    res = parse_tally_response(raw)
    out: Dict[str, Any] = {"xml_valid": True, "shape": shape, "records": len(res.records), "errors_total": len(res.errors), "errors": res.errors[:5]}
    if res.records:
        out["stats"] = _stats(res.records)
    if shape == "unknown":
        out["tags"] = _tag_census(raw)
    return out


def import_fixture(src: str, dest_dir: str, name: str) -> Dict[str, Any]:
    """Turn a capture from a real Tally into a parser test fixture: re-redact (so even an unredacted file is made safe), refuse anything the parser
    cannot fully read, and save as ``real_<name>.xml``. The replay test in tests/connector/test_tally_check.py then guards it forever."""
    from dueclock.core.tally_parse import parse_tally_response
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name):
        raise ValueError("name may only contain letters, digits, dot, dash and underscore (max 40)")
    with open(src, "rb") as f:
        from .tally_client import decode_response
        text = decode_response(f.read())
    safe = redact_xml(text)
    res = parse_tally_response(safe)
    problems = []
    if not res.records:
        problems.append("the parser found no bills in this capture")
    if res.errors:
        problems.append(f"{len(res.errors)} rows could not be read (first: {res.errors[:2]})")
    out: Dict[str, Any] = {"ok": not problems, "problems": problems, "records": len(res.records), "stats": _stats(res.records) if res.records else None}
    if problems:
        return out
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"real_{name}.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(safe)
    out["path"] = path
    return out


def save_profile(config_path: str, profile: Dict[str, str]) -> None:
    """Write the working variants into the config JSON, leaving every other field (including the protected key) untouched."""
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["tally_profile"] = {**data.get("tally_profile", {}), **profile}
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def render_text(rep: Dict[str, Any]) -> str:
    lines = [f"Tally check: {rep['verdict']}  (connector {rep['connector_version']}, {rep['when']})", f"Tally: {rep['tally']}"]
    for part in ("companies", "bills"):
        for a in rep[part]["attempts"]:
            lines.append(f"  {part}/{a['variant']}: " + ", ".join(f"{k}={v}" for k, v in a.items() if k not in ("variant", "stats", "tags", "errors")))
    for f in rep["findings"]:
        lines.append(f"[{f['level'].upper()}] {f['code']}: {f['message']}")
    if rep["captures"]:
        lines.append("Redacted captures: " + ", ".join(rep["captures"]))
    return "\n".join(lines)
