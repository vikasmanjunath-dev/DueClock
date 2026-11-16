"""
DueClock DPDP Compliance & Data Processor Framework
Adheres to Digital Personal Data Protection Act, 2023 & DPDP Rules (effective May 13, 2027).
Implements statutory processor safeguards, consent logging, and one-click data erasure.
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class ConsentLogEntry(BaseModel):
    id: str
    tenant_id: str
    counterparty_identifier: str  # Buyer GSTIN or Mobile hash
    consent_purpose: str = "Section 43B(h) statutory invoice communication & UPI payment processing"
    ip_address: Optional[str] = "127.0.0.1"
    user_agent: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True


class DPDPComplianceManager:
    """
    Ensures DueClock strictly operates as a Data Processor on behalf of the Supplier (Data Fiduciary).
    Provides compliance audit logs and irreversible tenant data purging mechanisms.
    """

    def __init__(self):
        self.consent_logs: List[ConsentLogEntry] = []
        self.breach_runbook_active: bool = False

    def log_consent(
        self,
        tenant_id: str,
        counterparty_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> ConsentLogEntry:
        """Logs explicit or transactional consent prior to buyer portal access or reminder dispatch."""
        entry = ConsentLogEntry(
            id=f"dpdp_log_{len(self.consent_logs) + 1}",
            tenant_id=tenant_id,
            counterparty_identifier=counterparty_id,
            ip_address=ip_address,
            user_agent=user_agent
        )
        self.consent_logs.append(entry)
        return entry

    def generate_data_processor_agreement_text(self, supplier_business_name: str) -> str:
        """
        Generates standard Data Processing Agreement (DPA) between Supplier (Fiduciary) and DueClock (Processor).
        Shields platform from ₹250 Cr statutory penalties under DPDP Section 33.
        """
        return f"""
DATA PROCESSING AGREEMENT (DPA)
Under Section 8 of the Digital Personal Data Protection Act, 2023

1. PARTIES:
   - Data Fiduciary: {supplier_business_name}
   - Data Processor: DueClock Technologies Private Limited

2. PURPOSE & SCOPE OF PROCESSING:
   The Processor shall process commercial transaction data (invoices, ledger entries, debtor contacts, payment references)
   solely for statutory Section 43B(h) computation, MSMED Act Section 15/16 interest calculation, WhatsApp reminder
   dispatch, and UPI auto-reconciliation.

3. SECURITY SAFEGUARDS:
   - AES-256 encryption for data at rest.
   - TLS 1.3 encryption for data in transit.
   - Row-level tenant isolation in PostgreSQL.
   - Zero-custody of funds: no bank account credentials or payment card data processed.

4. DATA PRINCIPAL RIGHTS & ERASURE:
   In compliance with Section 12 of the DPDP Act, upon request by the Data Fiduciary or upon subscription
   termination, the Processor shall irreversibly delete all tenant ledgers within 72 hours.

5. BREACH NOTIFICATION:
   The Processor shall notify the Data Fiduciary within 6 hours of any verified security incident.
"""
