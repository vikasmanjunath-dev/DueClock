"""Reviewer test pack: every statutory-clock expectation in one workbook a domain reviewer (CA / MSME-law specialist) can check.

Built from the LIVE engine, so it cannot drift from the code. Each case shows the inputs, the plain-English rule under test, what the
engine says (and its trace), and blank reviewer columns. A signed-back workbook is read into ``reviewer_decisions.json``; a test then
holds the engine to those decisions (Agree = pinned as a regression, Disagree + correct value = the engine must match it).

    python -m dueclock.core.review_pack build  pack.xlsx
    python -m dueclock.core.review_pack check  signed.xlsx --out tests/core/reviewer_decisions.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .clock import ENGINE_VERSION, InvoiceFacts, RatePolicy, SupplierFacts, evaluate
from .rates import BANK_RATE_HISTORY, HISTORY_VERIFIED_THROUGH, RateTable

PACK_VERSION = "1"
VERDICTS = ["Agree", "Disagree", "Unsure"]
D = date.fromisoformat


@dataclass(frozen=True)
class Case:
    id: str
    topic: str
    rule: str                                   # plain-English statement of what the engine is expected to do
    supplier: Dict[str, Any]
    invoice: Dict[str, Any]
    as_of: str
    rate_policy: str = "at_due_date"
    open_question: str = ""                     # id of the related open question, if any


def _s(cat="MICRO", act="MANUFACTURING", reg="2024-01-10"):
    return {"category": cat, "activity": act, "udyam_registration_date": reg}


def _i(inv="2026-04-01", out=100000.0, deliv="2026-04-01", acc=None, agr=False, days=0):
    return {"invoice_date": inv, "delivery_date": deliv, "acceptance_date": acc, "outstanding": out, "has_written_agreement": agr, "agreed_credit_days": days}


CASES: List[Case] = [
    Case("R01", "Due date", "No written agreement: payment is due 15 days after delivery.", _s(), _i(), "2026-04-10"),
    Case("R02", "Due date", "The clock starts on the delivery date, not the invoice date, when delivery is later.", _s(), _i(inv="2026-04-01", deliv="2026-04-05"), "2026-04-10"),
    Case("R03", "Due date", "If the delivery date is missing, the invoice date is used.", _s(), _i(deliv=None), "2026-04-10"),
    Case("R04", "Due date", "A written agreement for 45 days gives a 45-day due date.", _s(), _i(agr=True, days=45), "2026-04-10"),
    Case("R05", "Due date", "An agreement for more than 45 days is capped at 45 and flagged.", _s(), _i(agr=True, days=60), "2026-04-10"),
    Case("R06", "Due date", "An agreement for less than 45 days (30) is respected.", _s(), _i(agr=True, days=30), "2026-04-10"),
    Case("R07", "Due date", "Agreement ticked but no number of days: falls back to 15 days and is flagged.", _s(), _i(agr=True, days=0), "2026-04-10"),
    Case("R08", "Coverage", "A trader seller is not covered: no interest and no year-end exposure.", _s(act="TRADING"), _i(), "2026-06-30"),
    Case("R09", "Coverage", "A medium or large seller is not covered.", _s(cat="MEDIUM"), _i(), "2026-06-30"),
    Case("R10", "Coverage", "Unknown category or activity: coverage is shown as unknown, never assumed.", _s(cat="UNKNOWN", act="UNKNOWN"), _i(), "2026-06-30"),
    Case("R11", "Overdue", "On the due date itself the invoice is not overdue.", _s(), _i(), "2026-04-16"),
    Case("R12", "Interest", "One day overdue: interest at 3x the bank rate, pro-rata for the day.", _s(), _i(), "2026-04-17"),
    Case("R13", "Interest", "Exactly 30 days overdue: one monthly rest.", _s(), _i(), "2026-05-16"),
    Case("R14", "Interest", "58 days overdue: one full monthly rest plus 28 days pro-rata.", _s(), _i(), "2026-06-13"),
    Case("R15", "Interest", "Interest runs on the outstanding balance only (part-paid invoice).", _s(), _i(out=60000.0), "2026-05-16"),
    Case("R16", "Interest", "A fully paid invoice has no overdue days and no interest.", _s(), _i(out=0.0), "2026-06-30"),
    Case("R17", "Year-end", "Due exactly on 31 March, not yet paid on 31 March: not overdue, so no exposure.", _s(), _i(inv="2026-03-16", deliv="2026-03-16"), "2026-03-31"),
    Case("R18", "Year-end", "Overdue by 1 day on 31 March and unpaid: flagged as year-end exposure.", _s(), _i(inv="2026-03-15", deliv="2026-03-15"), "2026-03-31", open_question="Q3"),
    Case("R19", "Year-end", "Due after the year-end (14 April): no exposure for this year.", _s(), _i(inv="2026-03-30", deliv="2026-03-30"), "2026-03-31"),
    Case("R20", "Open rule", "An acceptance date is recorded but the engine ignores it and starts from delivery (flagged).", _s(), _i(acc="2026-04-06"), "2026-04-20", open_question="Q1"),
    Case("R21", "Open rule", "Seller registered on Udyam AFTER the supply date: engine still treats it as covered but flags it.", _s(reg="2026-05-01"), _i(), "2026-06-30", open_question="Q2"),
    Case("R22", "Open rule", "Registration date missing: engine treats the seller as covered but flags it.", _s(reg=None), _i(), "2026-06-30", open_question="Q2"),
    Case("R23", "Rate", "Bank rate falls inside the overdue period (cut on 6 Jun 2025); the rate on the due date is used throughout.", _s(), _i(inv="2025-05-05", deliv="2025-05-05"), "2025-07-19", open_question="Q4"),
    Case("R24", "Rate", "Same invoice, but each month is priced at the rate in force in that month.", _s(), _i(inv="2025-05-05", deliv="2025-05-05"), "2025-07-19", rate_policy="through_period", open_question="Q4"),
]

OPEN_QUESTIONS = [
    ("Q1", "Acceptance / deemed acceptance", "Should the 15-day period run from the day of acceptance (or deemed acceptance) rather than the delivery date, and how should an objection be handled?", "Engine starts from delivery (invoice date if missing); an acceptance date is stored but ignored. Flag: ACCEPTANCE_DATE_IGNORED."),
    ("Q2", "Udyam registration date", "Is a seller covered for supplies made BEFORE its Udyam registration date? What if the date is unknown?", "Engine treats the seller as covered and flags REGISTERED_AFTER_SUPPLY or REGISTRATION_DATE_MISSING."),
    ("Q3", "Year-end exposure (Sec 43B(h))", "What exactly makes a payable a year-end exposure: overdue beyond the statutory limit on 31 March and unpaid? How does payment-basis timing (paid after year-end, before return filing) change it?", "Engine flags exposure when covered, overdue and unpaid on the as-of date (31 March for the year-end view)."),
    ("Q4", "Which bank rate applies", "Is interest computed at the bank rate in force on the due date for the whole period, or at each month's rate?", "Default: rate on the due date (at_due_date). Alternative implemented: through_period."),
    ("Q5", "Interest method", "Confirm: compound interest with monthly rests, month = 30 days, remainder pro-rata, on the outstanding balance (including GST as shown in Tally).", "Engine does exactly this."),
    ("Q6", "Written agreement", "Must the written agreement pre-date the supply, and what proof is enough? Currently only a yes/no and agreed days are held.", "Engine accepts the flag as entered by the owner."),
    ("Q7", "Rate table", "Please verify each bank-rate row on the Rates sheet against RBI's notification (Table 46).", "All rows except two 2020 rows are unconfirmed; engine flags RATE_TABLE_UNCONFIRMED and RATE_TABLE_STALE."),
]


def run_case(c: Case, rates: Optional[RateTable] = None) -> Dict[str, Any]:
    s = SupplierFacts(c.supplier["category"], c.supplier["activity"], D(c.supplier["udyam_registration_date"]) if c.supplier.get("udyam_registration_date") else None)
    inv = c.invoice
    f = InvoiceFacts(D(inv["invoice_date"]), inv["outstanding"], D(inv["delivery_date"]) if inv.get("delivery_date") else None,
                     D(inv["acceptance_date"]) if inv.get("acceptance_date") else None, inv["has_written_agreement"], inv["agreed_credit_days"])
    r = evaluate(f, s, D(c.as_of), rates or RateTable.default(), rate_policy=RatePolicy(c.rate_policy))
    return r.to_dict()


def fingerprint(c: Case, result: Dict[str, Any]) -> str:
    keys = ("coverage", "base_date", "due_date", "overdue_days", "annual_interest_rate_pct", "interest", "disallowance_exposure", "review_flags")
    blob = json.dumps({"c": c.__dict__, "r": {k: result[k] for k in keys}}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:10]


def build_rows(rates: Optional[RateTable] = None) -> List[Dict[str, Any]]:
    rows = []
    for c in CASES:
        r = run_case(c, rates)
        rows.append({"case": c, "result": r, "fingerprint": fingerprint(c, r)})
    return rows


# ---------------------------------------------------------------- workbook
CASE_COLS = ["ID", "Topic", "Rule being tested", "Seller (category / activity / Udyam date)", "Invoice date", "Delivery date", "Acceptance date",
             "Outstanding (INR)", "Written agreement / days", "Check as of", "Rate policy", "ENGINE: coverage", "ENGINE: due date", "ENGINE: days overdue",
             "ENGINE: rate % p.a.", "ENGINE: interest (INR)", "ENGINE: year-end exposure", "ENGINE: flags", "How the engine got there",
             "Open question", "REVIEWER: verdict", "REVIEWER: correct due date", "REVIEWER: correct interest (INR)", "REVIEWER: comment", "Ref"]
HEADER_ROW = 5


def build_workbook(path: str, rates: Optional[RateTable] = None) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    rates = rates or RateTable.default()
    wb = Workbook()
    head = PatternFill("solid", fgColor="1F3A5F")
    rev = PatternFill("solid", fgColor="FFF2CC")
    wrap = Alignment(wrap_text=True, vertical="top")

    def style_header(ws, row, ncols, reviewer_from=None):
        for ci in range(1, ncols + 1):
            c = ws.cell(row=row, column=ci)
            c.font, c.alignment = Font(bold=True, color="FFFFFF"), Alignment(wrap_text=True, vertical="center")
            c.fill = PatternFill("solid", fgColor="7F6000") if reviewer_from and ci >= reviewer_from else head

    ws = wb.active
    ws.title = "Read me"
    for i, line in enumerate([
        "DueClock statutory-clock reviewer pack", "",
        f"Engine version: {ENGINE_VERSION}   |   Pack version: {PACK_VERSION}   |   Rate table checked through: {HISTORY_VERIFIED_THROUGH.isoformat()}", "",
        "What this is: each row on 'Cases' is one situation, the plain-English rule we implemented, and what the software produces for it.",
        "Your job: for each case choose Agree / Disagree / Unsure in the yellow column. If you disagree, type the correct due date (YYYY-MM-DD) and/or interest (INR) and a short reason.",
        "'Open questions': rules we could not settle ourselves. Please give a decision for each (the software currently does what the 'current behaviour' column says).",
        "'Rates': the bank-rate history used. Mark each row Verified (or give the correct rate).",
        "Please do not edit the grey columns or the ID / Ref columns: the Ref code tells us if the software changed after this pack was issued.",
        "Interest uses 3x the bank rate with monthly rests; amounts are on INR 1,00,000 unless stated. This is a software check, not legal advice from us.",
    ], 1):
        ws.cell(row=i, column=1, value=line).alignment = Alignment(wrap_text=True, vertical="top")
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 130

    cs = wb.create_sheet("Cases")
    cs["A1"], cs["B1"] = "Reviewer name", None
    cs["A2"], cs["B2"] = "Review date", None
    cs["A3"], cs["B3"] = "Instructions", "Fill only the yellow columns (U to X). Dates as YYYY-MM-DD."
    for r in (1, 2):
        cs.cell(row=r, column=1).font = Font(bold=True)
        cs.cell(row=r, column=2).fill = rev
    for ci, name in enumerate(CASE_COLS, 1):
        cs.cell(row=HEADER_ROW, column=ci, value=name)
    style_header(cs, HEADER_ROW, len(CASE_COLS), reviewer_from=21)
    for ri, item in enumerate(build_rows(rates), HEADER_ROW + 1):
        c, r = item["case"], item["result"]
        sup, inv = c.supplier, c.invoice
        vals = [c.id, c.topic, c.rule, f"{sup['category']} / {sup['activity']} / {sup.get('udyam_registration_date') or 'unknown'}", inv["invoice_date"], inv.get("delivery_date") or "(missing)",
                inv.get("acceptance_date") or "-", inv["outstanding"], f"{'Yes' if inv['has_written_agreement'] else 'No'} / {inv['agreed_credit_days']}", c.as_of,
                "rate in force each month" if c.rate_policy == "through_period" else "rate on the due date", r["coverage"], r["due_date"], r["overdue_days"],
                r["annual_interest_rate_pct"], r["interest"], "YES" if r["disallowance_exposure"] else "no", ", ".join(r["review_flags"]) or "-", "\n".join(r["trace"]),
                c.open_question or "-", None, None, None, None, item["fingerprint"]]
        for ci, v in enumerate(vals, 1):
            cell = cs.cell(row=ri, column=ci, value=v)
            cell.alignment = wrap
            if 21 <= ci <= 24:
                cell.fill = rev
    dv = DataValidation(type="list", formula1='"' + ",".join(VERDICTS) + '"', allow_blank=True)
    cs.add_data_validation(dv)
    dv.add(f"U{HEADER_ROW + 1}:U{HEADER_ROW + len(CASES)}")
    for ci, w in enumerate([6, 11, 46, 30, 12, 12, 12, 12, 14, 12, 18, 16, 12, 9, 9, 12, 10, 28, 52, 9, 12, 14, 14, 34, 11], 1):
        cs.column_dimensions[get_column_letter(ci)].width = w
    cs.freeze_panes = cs.cell(row=HEADER_ROW + 1, column=4)

    qs = wb.create_sheet("Open questions")
    for ci, name in enumerate(["ID", "Topic", "Question for the reviewer", "What the software does today", "REVIEWER: decision / correct rule", "REVIEWER: comment"], 1):
        qs.cell(row=1, column=ci, value=name)
    style_header(qs, 1, 6, reviewer_from=5)
    for ri, q in enumerate(OPEN_QUESTIONS, 2):
        for ci, v in enumerate(list(q) + [None, None], 1):
            cell = qs.cell(row=ri, column=ci, value=v)
            cell.alignment = wrap
            if ci >= 5:
                cell.fill = rev
    for ci, w in enumerate([6, 28, 70, 60, 40, 40], 1):
        qs.column_dimensions[get_column_letter(ci)].width = w

    rs = wb.create_sheet("Rates")
    for ci, name in enumerate(["Effective from", "Bank rate % (= MSF rate)", "Source / status", "Confirmed against an RBI document", "REVIEWER: verified?", "REVIEWER: correct rate % (if different)"], 1):
        rs.cell(row=1, column=ci, value=name)
    style_header(rs, 1, 6, reviewer_from=5)
    for ri, (d, rate, conf, src) in enumerate(BANK_RATE_HISTORY, 2):
        for ci, v in enumerate([d.isoformat(), rate, src, "yes" if conf else "no", None, None], 1):
            cell = rs.cell(row=ri, column=ci, value=v)
            if ci >= 5:
                cell.fill = rev
    dv2 = DataValidation(type="list", formula1='"Verified,Wrong,Unsure"', allow_blank=True)
    rs.add_data_validation(dv2)
    dv2.add(f"E2:E{len(BANK_RATE_HISTORY) + 1}")
    for ci, w in enumerate([14, 22, 60, 20, 18, 22], 1):
        rs.column_dimensions[get_column_letter(ci)].width = w

    meta = wb.create_sheet("meta")
    meta["A1"], meta["B1"] = "pack_version", PACK_VERSION
    meta["A2"], meta["B2"] = "engine_version", ENGINE_VERSION
    meta["A3"], meta["B3"] = "issued", datetime.now().strftime("%Y-%m-%d")
    meta.sheet_state = "hidden"
    wb.save(path)
    return path


# ---------------------------------------------------------------- read back
def _txt(v: Any) -> str:
    if v is None:
        return ""
    return v.date().isoformat() if isinstance(v, datetime) else str(v).strip()


def read_decisions(path: str, rates: Optional[RateTable] = None) -> Dict[str, Any]:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    cs = wb["Cases"]
    cur = {it["case"].id: it for it in build_rows(rates)}
    out: Dict[str, Any] = {"pack_version": PACK_VERSION, "engine_version_now": ENGINE_VERSION, "reviewer": _txt(cs["B1"].value), "review_date": _txt(cs["B2"].value),
                           "cases": {}, "questions": {}, "rates": {}, "problems": []}
    meta = wb["meta"] if "meta" in wb.sheetnames else None
    out["engine_version_issued"] = _txt(meta["B2"].value) if meta else ""
    idx = {_txt(cs.cell(row=HEADER_ROW, column=ci).value): ci for ci in range(1, len(CASE_COLS) + 1)}
    for r in range(HEADER_ROW + 1, cs.max_row + 1):
        cid = _txt(cs.cell(row=r, column=idx["ID"]).value)
        if not cid:
            continue
        g = lambda name: _txt(cs.cell(row=r, column=idx[name]).value)
        verdict = g("REVIEWER: verdict")
        entry = {"verdict": verdict, "correct_due_date": g("REVIEWER: correct due date"), "correct_interest": g("REVIEWER: correct interest (INR)"), "comment": g("REVIEWER: comment"),
                 "engine_due_date": g("ENGINE: due date"), "engine_interest": g("ENGINE: interest (INR)")}
        if cid not in cur:
            out["problems"].append(f"{cid}: not a case in this version of the software")
        elif g("Ref") != cur[cid]["fingerprint"]:
            entry["engine_changed_since_issue"] = True
            out["problems"].append(f"{cid}: the engine's answer changed since this pack was issued; re-issue before relying on this verdict")
        if verdict and verdict not in VERDICTS:
            out["problems"].append(f"{cid}: verdict '{verdict}' is not Agree/Disagree/Unsure")
        if verdict == "Disagree" and not (entry["correct_due_date"] or entry["correct_interest"] or entry["comment"]):
            out["problems"].append(f"{cid}: Disagree without a correct value or a comment")
        if entry["correct_due_date"]:
            try:
                D(entry["correct_due_date"])
            except ValueError:
                out["problems"].append(f"{cid}: correct due date '{entry['correct_due_date']}' is not YYYY-MM-DD")
        if entry["correct_interest"]:
            try:
                float(entry["correct_interest"].replace(",", ""))
            except ValueError:
                out["problems"].append(f"{cid}: correct interest '{entry['correct_interest']}' is not a number")
        out["cases"][cid] = entry
    qs = wb["Open questions"]
    for r in range(2, qs.max_row + 1):
        qid = _txt(qs.cell(row=r, column=1).value)
        if qid:
            out["questions"][qid] = {"decision": _txt(qs.cell(row=r, column=5).value), "comment": _txt(qs.cell(row=r, column=6).value)}
    rs = wb["Rates"]
    for r in range(2, rs.max_row + 1):
        d = _txt(rs.cell(row=r, column=1).value)
        if d:
            out["rates"][d] = {"verdict": _txt(rs.cell(row=r, column=5).value), "correct_rate": _txt(rs.cell(row=r, column=6).value)}
    return out


def summarize(dec: Dict[str, Any]) -> Dict[str, Any]:
    c = dec["cases"]
    count = lambda v: sum(1 for x in c.values() if x["verdict"] == v)
    return {"cases": len(c), "agree": count("Agree"), "disagree": count("Disagree"), "unsure": count("Unsure"), "blank": sum(1 for x in c.values() if not x["verdict"]),
            "questions_answered": sum(1 for q in dec["questions"].values() if q["decision"]), "questions_total": len(dec["questions"]),
            "rates_verified": sum(1 for r in dec["rates"].values() if r["verdict"] == "Verified"), "rates_total": len(dec["rates"]),
            "problems": len(dec["problems"]), "complete": all(x["verdict"] in ("Agree", "Disagree") for x in c.values()) and not dec["problems"]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="review_pack")
    p.add_argument("command", choices=["build", "check"])
    p.add_argument("path")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    if a.command == "build":
        print("wrote", build_workbook(a.path))
        return 0
    dec = read_decisions(a.path)
    s = summarize(dec)
    print(json.dumps(s, indent=2))
    for x in dec["problems"]:
        print("problem:", x, file=sys.stderr)
    for cid, e in dec["cases"].items():
        if e["verdict"] == "Disagree":
            print(f"DISAGREE {cid}: engine due {e['engine_due_date']} / interest {e['engine_interest']} -> reviewer due {e['correct_due_date'] or '-'} / interest {e['correct_interest'] or '-'}  {e['comment']}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(dec, f, indent=2)
        print("wrote", a.out)
    return 0 if s["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
