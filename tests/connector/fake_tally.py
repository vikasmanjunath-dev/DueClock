"""A tiny fake TallyPrime HTTP server for connector tests (real sockets, no mocks of our own code)."""
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional


def nested_xml(bills: List[dict]) -> str:
    parts = []
    for b in bills:
        parts.append(
            f"<BILLOUTSTANDING><BILLDATE>{b['date']}</BILLDATE><BILLREF>{b['ref']}</BILLREF><BILLPARTY>{b['party']}</BILLPARTY>"
            f"<BILLOPENING>{b['opening']:,.2f} Dr</BILLOPENING><BILLCL>{b['closing']:,.2f} Dr</BILLCL>"
            f"<BILLCREDITPERIOD>{b.get('credit', 0)} Days</BILLCREDITPERIOD></BILLOUTSTANDING>")
    return "<ENVELOPE><BODY><DATA><COLLECTION>" + "".join(parts) + "</COLLECTION></DATA></BODY></ENVELOPE>"


def flat_xml(bills: List[dict]) -> str:
    parts = []
    for b in bills:
        parts.append(f"<BILLFIXED><BILLDATE>{b['date']}</BILLDATE><BILLREF>{b['ref']}</BILLREF><BILLPARTY>{b['party']}</BILLPARTY></BILLFIXED>"
                     f"<BILLCL>{b['closing']:,.2f}</BILLCL><BILLDUE>{b.get('due', b['date'])}</BILLDUE>")
    return "<ENVELOPE>" + "".join(parts) + "</ENVELOPE>"


def bill(n: int, party: str = "Zenith Tooling", opening: float = 100000.0, closing: Optional[float] = None, date: str = "20261001", credit: int = 0) -> dict:
    return dict(ref=f"INV/{n}", party=party, opening=opening, closing=opening if closing is None else closing, date=date, credit=credit)


class FakeTally:
    def __init__(self, body: str = "", banner: str = "TallyPrime Server is Running", status: int = 200, utf16: bool = False):
        self.body, self.banner, self.status, self.utf16 = body, banner, status, utf16
        self.by_company: Dict[str, str] = {}
        self.responder = None                      # optional fn(raw_request) -> (status, text) for per-request replies
        self.requests: List[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                data = outer.banner.encode("utf-8")
                self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8")
                outer.requests.append(raw)
                try:
                    comp = ET.fromstring(raw).findtext(".//SVCURRENTCOMPANY")
                except ET.ParseError:
                    comp = None
                status, text = (outer.responder(raw) if outer.responder else (outer.status, outer.by_company.get(comp, outer.body)))
                data = ("﻿" + text).encode("utf-16") if outer.utf16 else text.encode("utf-8")
                self.send_response(status); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown(); self.server.server_close()
