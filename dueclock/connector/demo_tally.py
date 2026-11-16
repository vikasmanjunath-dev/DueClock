"""A pretend TallyPrime for rehearsing the connector on a computer that has no Tally (a Mac, a laptop).

    python3 -m dueclock.connector.demo_tally --port 9000            # then, in another window:  dueclock-connector tally-check
It speaks the SAME request shapes our connector sends and answers with the reply shapes we ASSUME real TallyPrime uses, so it proves our own
code end to end but says NOTHING about the real product: the real check is `tally-check` on a PC with TallyPrime (see TALLY_CHECK_RUNBOOK.md).
Only for local rehearsal: it listens on 127.0.0.1 and holds fictional data.
"""
from __future__ import annotations

import argparse
import re
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Tuple

BUYERS = ["Zenith Tooling Systems Pvt Ltd", "Kaveri Hydraulics LLP", "Mysore Castings", "Bharat Fasteners", "Lakshmi Auto Components"]


def sample_bills(today: date, n: int = 15) -> List[Tuple[date, str, str, float, date]]:
    """(bill date, reference, party, pending amount, due date). Fictional; spread over ~120 days so some are not yet due and some are overdue."""
    out = []
    for i in range(n):
        d = today - timedelta(days=8 * (i + 1))
        out.append((d, f"DEMO/{i + 1:03d}", BUYERS[i % len(BUYERS)], round(25000 + 13750.5 * ((i * 7) % 9), 2), d + timedelta(days=30)))
    return out


def tally_date(d: date) -> str:
    return f"{d.day}-{d.strftime('%b')}-{d.strftime('%y')}"


def fmt_amount(x: float, style: str) -> str:
    s = f"{x:,.2f}"                                          # western grouping is fine: the parser strips separators
    return {"plain": s, "dr": s + " Dr", "negative": "-" + s}[style]


def bills_xml(bills, style: str = "plain") -> str:
    parts = ["<ENVELOPE>"]
    for d, ref, party, amt, due in bills:
        parts.append(f"<BILLFIXED><BILLDATE>{tally_date(d)}</BILLDATE><BILLREF>{ref}</BILLREF><BILLPARTY>{party}</BILLPARTY></BILLFIXED>"
                     f"<BILLCL>{fmt_amount(amt, style)}</BILLCL><BILLDUE>{tally_date(due)}</BILLDUE>")
    parts.append("</ENVELOPE>")
    return "".join(parts)


COMPANIES_XML = "<ENVELOPE><BODY><DATA><COLLECTION><COMPANY><NAME>Demo Engineering Works (fictional)</NAME></COMPANY></COLLECTION></DATA></BODY></ENVELOPE>"


def make_server(port: int = 9000, *, utf16: bool = False, amount_style: str = "plain", plain_only: bool = False, today: date | None = None) -> ThreadingHTTPServer:
    """plain_only: refuse requests carrying EXPLODEFLAG, to rehearse the connector's fallback to the second request variant."""
    today = today or date.today()
    bills = bills_xml(sample_bills(today), amount_style)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status: int, text: str) -> None:
            data = ("﻿" + text).encode("utf-16") if utf16 else text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, "TallyPrime Server is Running")

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "ignore")
            if "<IMPORTDATA" in raw.upper() or "<TALLYREQUEST>IMPORT" in raw.upper():
                return self._send(200, "<RESPONSE><LINEERROR>demo server is read-only</LINEERROR></RESPONSE>")
            if "Bills Receivable" in raw:
                if plain_only and "<EXPLODEFLAG>" in raw:
                    return self._send(500, "demo: this request variant is not accepted")
                return self._send(200, bills)
            if "<TYPE>Collection</TYPE>" in raw and re.search(r"<ID>(List of Companies|Company)</ID>", raw):
                return self._send(200, COMPANIES_XML)
            self._send(200, "<ENVELOPE><BODY>Unknown report</BODY></ENVELOPE>")

    return ThreadingHTTPServer(("127.0.0.1", port), H)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="demo_tally")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--utf16", action="store_true", help="reply in UTF-16 like some Tally versions")
    p.add_argument("--amount-style", choices=["plain", "dr", "negative"], default="plain")
    p.add_argument("--plain-only", action="store_true", help="reject the first request variant (rehearses the fallback)")
    a = p.parse_args(argv)
    srv = make_server(a.port, utf16=a.utf16, amount_style=a.amount_style, plain_only=a.plain_only)
    print(f"Demo Tally (fictional data, NOT a real Tally) listening on http://127.0.0.1:{a.port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
