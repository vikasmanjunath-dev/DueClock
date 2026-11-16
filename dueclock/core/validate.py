"""Validation of the seller's own business profile (what the statutory clock depends on). Returns field-level messages; never raises."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .buyer_page import valid_vpa
from .messaging import normalize_mobile

CATEGORIES = ("MICRO", "SMALL", "MEDIUM", "LARGE")
ACTIVITIES = ("MANUFACTURING", "SERVICES", "TRADING")
GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
UDYAM = re.compile(r"^UDYAM-[A-Z]{2}-\d{2}-\d{7}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,200}\.[A-Za-z]{2,}$")
EARLIEST_REGISTRATION = date(2006, 10, 2)          # the MSMED Act came into force on 2 Oct 2006 [from memory: confirm]


def clean_supplier(raw: Mapping[str, Any], today: date, *, strict: bool = True) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """``strict`` (the owner form) requires business name, category and activity; the API path leaves them optional like before."""
    v: Dict[str, Any] = {}
    err: Dict[str, str] = {}
    s = lambda k: (str(raw.get(k) or "")).strip()

    v["business_name"] = s("business_name")
    if not v["business_name"]:
        err["business_name"] = "Enter the business name as it should appear to buyers."
    elif len(v["business_name"]) > 120:
        err["business_name"] = "Keep the name under 120 characters."

    for key, allowed, label in (("category", CATEGORIES, "Choose Micro, Small, Medium or Large (as on your Udyam certificate)."),
                                ("activity", ACTIVITIES, "Choose Manufacturing, Services or Trading.")):
        val = s(key).upper() or "UNKNOWN"
        v[key] = val
        if val not in allowed and (strict or val != "UNKNOWN"):
            err[key] = label

    v["udyam_number"] = s("udyam_number").upper() or None
    if v["udyam_number"] and not UDYAM.match(v["udyam_number"]):
        err["udyam_number"] = "Format is UDYAM-XX-00-0000000 (state code, district, 7 digits)."

    v["udyam_registration_date"] = None
    if s("udyam_registration_date"):
        try:
            d = date.fromisoformat(s("udyam_registration_date"))
            if d > today:
                err["udyam_registration_date"] = "The registration date cannot be in the future."
            elif d < EARLIEST_REGISTRATION:
                err["udyam_registration_date"] = "That is before the MSMED Act came into force (2 Oct 2006). Check the date on your certificate."
            else:
                v["udyam_registration_date"] = d.isoformat()
        except ValueError:
            err["udyam_registration_date"] = "Enter the date as YYYY-MM-DD."

    v["gstin"] = s("gstin").upper() or None
    if v["gstin"] and not GSTIN.match(v["gstin"]):
        err["gstin"] = "A GSTIN has 15 characters, for example 29ABCDE1234F1Z5."

    v["upi_vpa"] = s("upi_vpa") or None
    if v["upi_vpa"] and not valid_vpa(v["upi_vpa"]):
        err["upi_vpa"] = "A UPI ID looks like name@bank. It must be YOUR account: buyers pay it directly."

    v["phone"] = None
    if s("phone"):
        m = normalize_mobile(s("phone"))
        if m is None:
            err["phone"] = "Enter a 10-digit Indian mobile number."
        else:
            v["phone"] = "+" + m

    v["email"] = s("email") or None
    if v["email"] and not EMAIL.match(v["email"]):
        err["email"] = "That email address does not look right."
    return v, err


def coverage_preview(v: Mapping[str, Any], invoices: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """Plain-language consequences of the profile: [(kind, text)] with kind ok / warn / bad."""
    from .clock import Coverage, SupplierFacts, assess_coverage
    reg: Optional[date] = date.fromisoformat(v["udyam_registration_date"]) if v.get("udyam_registration_date") else None
    cov = assess_coverage(SupplierFacts(v.get("category") or "UNKNOWN", v.get("activity") or "UNKNOWN", reg))
    out: List[Tuple[str, str]] = []
    if cov == Coverage.COVERED:
        out.append(("ok", "Covered: the 15/45-day payment clock, interest and the 43B(h) year-end view apply to your invoices."))
    elif cov == Coverage.NOT_COVERED_TRADER:
        out.append(("bad", "Not covered: a trader's sales fall outside the MSMED payment rules. Balances and reminders still work, but no statutory clock, interest or 43B(h) warning is shown."))
    elif cov == Coverage.NOT_COVERED_SIZE:
        out.append(("bad", "Not covered: medium and large sellers are outside the MSMED payment rules. Balances and reminders still work, but no statutory clock is shown."))
    else:
        out.append(("warn", "Not decided yet: choose your category and activity. Until then invoices show 'coverage unknown' and no interest."))
    if cov == Coverage.COVERED:
        if reg is None:
            out.append(("warn", "No registration date: invoices will carry a 'registration date missing' review flag."))
        else:
            early = sum(1 for i in invoices if i["invoice_date"] < reg.isoformat())
            if early:
                out.append(("warn", f"{early} invoice(s) are dated before your registration date; they are flagged for review (whether they are covered is a reviewer question)."))
    if not v.get("upi_vpa"):
        out.append(("warn", "No UPI ID: buyer pages will say 'ask the supplier for payment details' instead of showing a Pay button."))
    return out
