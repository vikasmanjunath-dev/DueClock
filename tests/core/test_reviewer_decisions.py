"""Holds the engine to a signed reviewer pack. Drop the output of `review_pack check signed.xlsx --out tests/core/reviewer_decisions.json`
next to this file: an 'Agree' case is pinned as a regression, a 'Disagree' case with a correct value makes this test fail until the engine
(or the documented rule) is changed to match the reviewer. Without the file these tests are skipped."""
import json
import os

import pytest

from dueclock.core import review_pack as rp

PATH = os.path.join(os.path.dirname(__file__), "reviewer_decisions.json")
pytestmark = pytest.mark.skipif(not os.path.exists(PATH), reason="no signed reviewer decisions yet")


def load():
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)


def test_engine_matches_reviewer_decisions():
    dec, rows, wrong = load(), {it["case"].id: it["result"] for it in rp.build_rows()}, []
    for cid, e in dec["cases"].items():
        r = rows[cid]
        if e["verdict"] == "Agree" and not e.get("engine_changed_since_issue"):
            want_due, want_int = e["engine_due_date"], float(e["engine_interest"])
        elif e["verdict"] == "Disagree":
            want_due, want_int = e["correct_due_date"] or r["due_date"], float(e["correct_interest"].replace(",", "")) if e["correct_interest"] else r["interest"]
        else:
            continue
        if r["due_date"] != want_due or abs(r["interest"] - want_int) > 0.01:
            wrong.append(f"{cid}: engine {r['due_date']} / {r['interest']}  vs reviewer {want_due} / {want_int}  ({e['verdict']}: {e['comment']})")
    assert not wrong, "engine differs from reviewer decisions:\n" + "\n".join(wrong)


def test_no_unresolved_problems_in_signed_pack():
    assert load()["problems"] == [], load()["problems"]
