"""
DueClock Tally Connector
Read-only desktop service and XML parser for TallyPrime.
Supports live HTTP XML sync (port 9000) and offline XML/JSON ledger export ingest.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, date
from typing import List, Dict, Any, Optional
import requests
import json
from dueclock.models import Invoice, BuyerProfile, InvoiceStatus


class TallyConnector:
    """
    TallyPrime Read-Only Ingestion Service.
    Safe: zero write operations, zero modifications to Tally ledgers or company databases.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9000, timeout_sec: int = 5):
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}"
        self.timeout = timeout_sec

    def is_tally_accessible(self) -> bool:
        """Pings TallyPrime XML server to check connectivity."""
        try:
            req_xml = """<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>List of Companies</ID></HEADER><BODY><DESC></DESC></BODY></ENVELOPE>"""
            headers = {"Content-Type": "text/xml;charset=utf-8"}
            resp = requests.post(self.url, data=req_xml, headers=headers, timeout=self.timeout)
            return resp.status_code == 200
        except Exception:
            return False

    def fetch_live_bills_outstanding(self) -> str:
        """Queries TallyPrime for Bills Outstanding via TDL XML."""
        req_xml = """
        <ENVELOPE>
            <HEADER>
                <VERSION>1</VERSION>
                <TALLYREQUEST>Export</TALLYREQUEST>
                <TYPE>Data</TYPE>
                <ID>Bills Outstanding</ID>
            </HEADER>
            <BODY>
                <DESC>
                    <STATICVARIABLES>
                        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                        <EXPLODEFLAG>Yes</EXPLODEFLAG>
                    </STATICVARIABLES>
                </DESC>
            </BODY>
        </ENVELOPE>
        """
        headers = {"Content-Type": "text/xml;charset=utf-8"}
        resp = requests.post(self.url, data=req_xml, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def parse_tally_date(self, date_str: Optional[str]) -> date:
        """Parses Tally date representations (e.g. '20261101', '1-Nov-2026', '2026-11-01')."""
        if not date_str:
            return date.today()
        
        date_str = date_str.strip()
        for fmt in ("%Y%m%d", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                pass
        return date.today()

    def parse_bills_xml(self, xml_content: str) -> List[Dict[str, Any]]:
        """
        Parses Tally XML payload into structured invoice records.
        Robust against variations in Tally schema versions.
        """
        root = ET.fromstring(xml_content)
        parsed_bills: List[Dict[str, Any]] = []

        # Tally bills can be located in BILLOUTSTANDING or VOUCHER elements
        bill_nodes = root.findall(".//BILLOUTSTANDING") or root.findall(".//BILL") or root.findall(".//VOUCHER")

        for node in bill_nodes:
            bill_date_elem = node.find("BILLDATE") or node.find("DATE")
            bill_ref_elem = node.find("BILLREF") or node.find("VOUCHERNUMBER") or node.find("REFERENCE")
            party_elem = node.find("BILLPARTY") or node.find("PARTYLEDGERNAME") or node.find("PARTYNAME")
            opening_amt_elem = node.find("BILLOPENGING") or node.find("OPENINGBALANCE") or node.find("AMOUNT")
            closing_amt_elem = node.find("BILLCL") or node.find("CLOSINGBALANCE") or node.find("PENDINGAMOUNT")
            credit_period_elem = node.find("BILLCREDITPERIOD")

            bill_date = self.parse_tally_date(bill_date_elem.text if bill_date_elem is not None else None)
            bill_ref = bill_ref_elem.text.strip() if bill_ref_elem is not None and bill_ref_elem.text else "N/A"
            party_name = party_elem.text.strip() if party_elem is not None and party_elem.text else "Unknown Buyer"

            # Parse amount
            def clean_amt(val_str: Optional[str]) -> float:
                if not val_str:
                    return 0.0
                clean = val_str.replace("₹", "").replace(",", "").replace("Dr", "").replace("Cr", "").strip()
                try:
                    return abs(float(clean))
                except ValueError:
                    return 0.0

            opening_amt = clean_amt(opening_amt_elem.text if opening_amt_elem is not None else None)
            closing_amt = clean_amt(closing_amt_elem.text if closing_amt_elem is not None else None)
            pending_amt = closing_amt if closing_amt > 0 else opening_amt

            credit_days = 15
            if credit_period_elem is not None and credit_period_elem.text:
                try:
                    raw = credit_period_elem.text.lower().replace("days", "").strip()
                    credit_days = int(raw)
                except ValueError:
                    credit_days = 15

            parsed_bills.append({
                "invoice_number": bill_ref,
                "invoice_date": bill_date,
                "buyer_name": party_name,
                "amount": opening_amt if opening_amt > 0 else pending_amt,
                "pending_amount": pending_amt,
                "agreed_credit_days": credit_days,
                "raw_node": node.tag
            })

        return parsed_bills

    def import_from_json_or_csv_export(self, records: List[Dict[str, Any]]) -> List[Invoice]:
        """
        Converts parsed export records into validated Invoice domain models.
        """
        invoices: List[Invoice] = []
        for idx, rec in enumerate(records):
            buyer = BuyerProfile(
                buyer_name=rec.get("buyer_name", "Unknown Buyer"),
                mobile=rec.get("mobile", "+919999999999"),
                gstin=rec.get("gstin"),
                pan=rec.get("pan")
            )
            inv_date = rec.get("invoice_date")
            if isinstance(inv_date, str):
                inv_date = self.parse_tally_date(inv_date)

            total_amt = float(rec.get("amount", 0.0))
            pending_amt = float(rec.get("pending_amount", total_amt))
            paid_amt = max(0.0, round(total_amt - pending_amt, 2))

            status = InvoiceStatus.PAID if pending_amt <= 0 else (
                InvoiceStatus.PARTIALLY_PAID if paid_amt > 0 else InvoiceStatus.PENDING
            )

            inv = Invoice(
                id=f"tally_inv_{idx + 1}_{rec.get('invoice_number', 'ref')}",
                invoice_number=rec.get("invoice_number", f"INV-{idx+1}"),
                invoice_date=inv_date,
                amount=total_amt,
                paid_amount=paid_amt,
                buyer=buyer,
                has_written_agreement=rec.get("has_written_agreement", False),
                agreed_credit_days=int(rec.get("agreed_credit_days", 15)),
                status=status
            )
            invoices.append(inv)
        return invoices
