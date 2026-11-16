"""Read-only client for TallyPrime's local HTTP XML interface (default port 9000).

Safety: every request passes ``assert_read_only`` before it is sent. Only Export requests are allowed;
anything that could create, alter or delete data raises ``ReadOnlyViolation``. [Request shapes are
unverified on a real TallyPrime: confirm in the Tally spike (TEC-01).]
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional
from xml.sax.saxutils import escape

import httpx

from dueclock.core.tally_parse import ParseResult, parse_tally_response

ALLOWED_REQUESTS = {"export", "export data", "export collection"}
FORBIDDEN_TAGS = {"IMPORTDATA", "REQUESTDATA", "TALLYMESSAGE"}


class ReadOnlyViolation(Exception):
    """A request that could change Tally data was refused."""


class TallyError(Exception):
    pass


def assert_read_only(xml: str) -> None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ReadOnlyViolation(f"unparseable request refused: {e}")
    kind = (root.findtext(".//TALLYREQUEST") or "").strip().lower()
    if kind not in ALLOWED_REQUESTS:
        raise ReadOnlyViolation(f"request type '{kind or 'missing'}' is not an export")
    for el in root.iter():
        if el.tag.upper() in FORBIDDEN_TAGS:
            raise ReadOnlyViolation(f"forbidden element {el.tag}")
        if any(a.upper() == "ACTION" for a in el.attrib):
            raise ReadOnlyViolation("ACTION attribute (create/alter/delete) refused")


def _env(report_id: str, company: Optional[str], kind: str = "Data", explode: bool = True) -> str:
    comp = f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>" if company else ""
    ex = "<EXPLODEFLAG>Yes</EXPLODEFLAG>" if explode else ""
    return (
        f"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>{kind}</TYPE>"
        f"<ID>{escape(report_id)}</ID></HEADER><BODY><DESC><STATICVARIABLES>"
        f"<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>{comp}{ex}"
        "</STATICVARIABLES></DESC></BODY></ENVELOPE>"
    )


# Request shapes are written from Tally's documented envelope (Export / Data or Collection / <ID>), not yet confirmed on a real TallyPrime.
# `tally-check` tries each variant against the real Tally and records the one that works in the config (`tally_profile`).
BILLS_VARIANTS = ("data_explode", "data_plain")
COMPANY_VARIANTS = ("data", "collection", "collection_company")


def bills_receivable_request(company: Optional[str] = None, variant: str = "data_explode") -> str:
    if variant not in BILLS_VARIANTS:
        raise ValueError(f"unknown bills request variant '{variant}'")
    return _env("Bills Receivable", company, "Data", explode=(variant == "data_explode"))


def companies_request(variant: str = "data") -> str:
    if variant == "data":
        return _env("List of Companies", None, "Data", explode=False)
    if variant == "collection":
        return _env("List of Companies", None, "Collection", explode=False)
    if variant == "collection_company":
        return _env("Company", None, "Collection", explode=False)
    raise ValueError(f"unknown company request variant '{variant}'")


def custom_request(template: str, company: Optional[str]) -> str:
    """A request written on site when none of the built-in variants works. ``{company}`` is replaced by the escaped company name (or removed)."""
    return template.replace("{company}", escape(company) if company else "")


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]|&#(?:[0-8]|1[1-2]|1[4-9]|2\d|3[01]);")


def decode_response(raw: bytes) -> str:
    """Tally replies in UTF-8 or UTF-16 and can contain control characters that break XML parsers."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-16", errors="replace")
    return _CTRL.sub("", text.lstrip("﻿"))


@dataclass
class ProbeResult:
    running: bool
    product: str = "unknown"          # TallyPrime | Tally.ERP 9 | unknown
    message: str = ""


class TallyClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9000, timeout: float = 20.0, http: Optional[httpx.Client] = None,
                 profile: Optional[dict] = None, bills_template: Optional[str] = None):
        self.base_url = f"http://{host}:{port}"
        self.http = http or httpx.Client(timeout=timeout)
        self.profile = dict(profile or {})
        self.bills_template = bills_template

    def probe(self) -> ProbeResult:
        try:
            r = self.http.get(self.base_url)
        except httpx.HTTPError as e:
            return ProbeResult(False, "unknown", f"not reachable: {type(e).__name__}")
        text = decode_response(r.content).strip()
        low = text.lower()
        if "tallyprime" in low:
            product = "TallyPrime"
        elif "tally.erp" in low:
            product = "Tally.ERP 9"
        else:
            product = "unknown"
        return ProbeResult("running" in low or product != "unknown", product, text[:120])

    def post(self, xml: str) -> str:
        assert_read_only(xml)
        try:
            r = self.http.post(self.base_url, content=xml.encode("utf-8"), headers={"Content-Type": "text/xml; charset=utf-8"})
        except httpx.HTTPError as e:
            raise TallyError(f"Tally request failed: {type(e).__name__}") from e
        if r.status_code != 200:
            raise TallyError(f"Tally returned HTTP {r.status_code}")
        return decode_response(r.content)

    def list_companies(self) -> List[str]:
        xml = self.post(companies_request(self.profile.get("companies", "data")))
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        names: List[str] = []
        for tag in ("COMPANYNAME", "NAME"):
            names = [e.text.strip() for e in root.iter(tag) if e.text and e.text.strip()]
            if names:
                break
        return list(dict.fromkeys(names))

    def fetch_bills(self, company: Optional[str] = None) -> ParseResult:
        if self.bills_template:
            return parse_tally_response(self.post(custom_request(self.bills_template, company)))
        return parse_tally_response(self.post(bills_receivable_request(company, self.profile.get("bills", "data_explode"))))
