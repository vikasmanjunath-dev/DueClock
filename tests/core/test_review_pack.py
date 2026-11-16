import json
import os

import pytest
from openpyxl import load_workbook

from dueclock.core import review_pack as rp

HAND = {  # independent hand calculations (3 x 5.50% = 16.5% p.a. in April-May 2026; monthly rate 1.375%)
    "R01": ("2026-04-16", 0, 0.0), "R02": ("2026-04-20", 0, 0.0), "R03": ("2026-04-16", 0, 0.0), "R04": ("2026-05-16", 0, 0.0),
    "R05": ("2026-05-16", 0, 0.0), "R06": ("2026-05-01", 0, 0.0), "R07": ("2026-04-16", 0, 0.0), "R11": ("2026-04-16", 0, 0.0),
    "R12": ("2026-04-16", 1, 45.83), "R13": ("2026-04-16", 30, 1375.00), "R14": ("2026-04-16", 58, 2675.98), "R15": ("2026-04-16", 30, 825.00),
    "R16": ("2026-04-16", 0, 0.0), "R17": ("2026-03-31", 0, 0.0), "R18": ("2026-03-30", 1, 45.83), "R19": ("2026-04-14", 0, 0.0),
}


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    p = tmp_path_factory.mktemp("rp") / "pack.xlsx"
    rp.build_workbook(str(p))
    return str(p)


def by_id():
    return {it["case"].id: it for it in rp.build_rows()}


def test_cases_match_independent_hand_calculations():
    rows = by_id()
    for cid, (due, overdue, interest) in HAND.items():
        r = rows[cid]["result"]
        assert (r["due_date"], r["overdue_days"]) == (due, overdue), cid
        assert r["interest"] == pytest.approx(interest, abs=0.01), cid


def test_case_expectations_on_coverage_exposure_and_flags():
    rows = by_id()
    cov = {k: rows[k]["result"]["coverage"] for k in ("R08", "R09", "R10")}
    assert cov == {"R08": "NOT_COVERED_TRADER", "R09": "NOT_COVERED_SIZE", "R10": "UNKNOWN"}
    assert all(rows[k]["result"]["interest"] == 0.0 and not rows[k]["result"]["disallowance_exposure"] for k in cov)
    assert [rows[k]["result"]["disallowance_exposure"] for k in ("R17", "R18", "R19")] == [False, True, False]
    flags = lambda k: rows[k]["result"]["review_flags"]
    assert "AGREEMENT_CAPPED" in flags("R05") and "AGREEMENT_DAYS_INVALID" in flags("R07") and "ACCEPTANCE_DATE_IGNORED" in flags("R20")
    assert "REGISTERED_AFTER_SUPPLY" in flags("R21") and "REGISTRATION_DATE_MISSING" in flags("R22")
    assert rows["R24"]["result"]["interest"] < rows["R23"]["result"]["interest"]            # later months priced at the lower 5.75% bank rate


def test_every_case_is_unique_and_every_open_question_is_defined():
    ids = [c.id for c in rp.CASES]
    assert len(ids) == len(set(ids))
    qids = {q[0] for q in rp.OPEN_QUESTIONS}
    assert {c.open_question for c in rp.CASES if c.open_question} <= qids


def test_workbook_structure(pack):
    wb = load_workbook(pack)
    assert wb.sheetnames == ["Read me", "Cases", "Open questions", "Rates", "meta"]
    cs = wb["Cases"]
    assert [cs.cell(row=rp.HEADER_ROW, column=i).value for i in (1, 21)] == ["ID", "REVIEWER: verdict"]
    assert cs.max_row == rp.HEADER_ROW + len(rp.CASES)
    assert len(cs.data_validations.dataValidation) == 1 and wb["Rates"].max_row == len(rp.BANK_RATE_HISTORY) + 1
    assert wb["meta"].sheet_state == "hidden"
    assert "ENGINE" in cs.cell(row=rp.HEADER_ROW, column=13).value and cs.cell(row=rp.HEADER_ROW + 1, column=13).value == "2026-04-16"


def fill(path, out, edits, default={"REVIEWER: verdict": "Agree"}):
    wb = load_workbook(path)
    cs = wb["Cases"]
    cs["B1"], cs["B2"] = "A. Reviewer", "2026-11-20"
    col = {cs.cell(row=rp.HEADER_ROW, column=i).value: i for i in range(1, 26)}
    for r in range(rp.HEADER_ROW + 1, cs.max_row + 1):
        cid = cs.cell(row=r, column=1).value
        for name, val in edits.get(cid, default).items():
            cs.cell(row=r, column=col[name], value=val)
    wb.save(out)
    return out


def test_roundtrip_all_agree(pack, tmp_path):
    dec = rp.read_decisions(fill(pack, str(tmp_path / "s.xlsx"), {}))
    s = rp.summarize(dec)
    assert dec["reviewer"] == "A. Reviewer" and s["agree"] == len(rp.CASES) and s["blank"] == 0 and s["complete"]
    assert s["questions_answered"] == 0 and s["rates_verified"] == 0                       # open items stay visible, not silently filled


def test_disagreement_with_correction_is_captured(pack, tmp_path):
    edits = {"R13": {"REVIEWER: verdict": "Disagree", "REVIEWER: correct interest (INR)": "1,400.00", "REVIEWER: comment": "rests are quarterly"}}
    dec = rp.read_decisions(fill(pack, str(tmp_path / "s.xlsx"), {**{c.id: {"REVIEWER: verdict": "Agree"} for c in rp.CASES}, **edits}))
    e = dec["cases"]["R13"]
    assert e["verdict"] == "Disagree" and e["correct_interest"] == "1,400.00" and float(e["engine_interest"]) == 1375.0
    assert rp.summarize(dec)["disagree"] == 1


def test_problems_are_reported(pack, tmp_path):
    base = {c.id: {"REVIEWER: verdict": "Agree"} for c in rp.CASES}
    base["R01"] = {"REVIEWER: verdict": "Disagree"}                                         # no value, no comment
    base["R02"] = {"REVIEWER: verdict": "Disagree", "REVIEWER: correct due date": "20-04-2026"}
    base["R03"] = {"REVIEWER: verdict": "Maybe"}
    dec = rp.read_decisions(fill(pack, str(tmp_path / "s.xlsx"), base))
    text = " | ".join(dec["problems"])
    assert "R01: Disagree without" in text and "R02: correct due date" in text and "R03: verdict 'Maybe'" in text
    assert not rp.summarize(dec)["complete"]


def test_blank_verdicts_make_the_pack_incomplete(pack, tmp_path):
    dec = rp.read_decisions(fill(pack, str(tmp_path / "s.xlsx"), {"R01": {"REVIEWER: verdict": "Agree"}}, default={}))
    s = rp.summarize(dec)
    assert s["agree"] == 1 and s["blank"] == len(rp.CASES) - 1 and not s["complete"]


def test_engine_change_after_issue_is_detected(pack, tmp_path):
    wb = load_workbook(pack)
    cs = wb["Cases"]
    ref_col = [i for i in range(1, 26) if cs.cell(row=rp.HEADER_ROW, column=i).value == "Ref"][0]
    cs.cell(row=rp.HEADER_ROW + 1, column=ref_col, value="stale00000")
    p = str(tmp_path / "stale.xlsx")
    wb.save(p)
    dec = rp.read_decisions(p)
    assert dec["cases"]["R01"].get("engine_changed_since_issue") and any("R01: the engine's answer changed" in x for x in dec["problems"])


def test_cli_build_check_and_exit_codes(pack, tmp_path, capsys):
    out = tmp_path / "dec.json"
    signed = fill(pack, str(tmp_path / "s.xlsx"), {})
    assert rp.main(["check", signed, "--out", str(out)]) == 0
    assert json.loads(out.read_text())["cases"]["R01"]["verdict"] == "Agree"
    assert rp.main(["check", pack]) == 1                                                    # unsigned pack is not complete
    assert "blank" in capsys.readouterr().out


def test_fingerprint_changes_when_the_answer_changes():
    c = rp.CASES[0]
    r = rp.run_case(c)
    assert rp.fingerprint(c, r) == rp.fingerprint(c, dict(r))
    assert rp.fingerprint(c, r) != rp.fingerprint(c, {**r, "interest": r["interest"] + 1})
