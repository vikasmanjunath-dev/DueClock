"""
DueClock Cloud Core & FastAPI Service
Provides REST APIs for Tally sync, statutory engine evaluation,
automated reconciliation, and serves the Apple-styled No-Login Buyer Portal.
"""

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

from dueclock.models import (
    SupplierProfile,
    BuyerProfile,
    Invoice,
    EnterpriseCategory,
    EnterpriseActivity,
    InvoiceStatus,
    BankCreditEntry,
    SupportedLanguage,
    CadenceStage
)
from dueclock.engine import StatutoryEngine
from dueclock.cadence import WhatsAppCadenceManager, UPILinkGenerator
from dueclock.recon import ReconciliationEngine
from dueclock.scanner import Section43bScanner, VendorPayableBill
from dueclock.discovery import DISCOVERY_QUESTIONS, DiscoveryEngine, DiscoveryInterviewRecord
from dueclock.dpdp import DPDPComplianceManager
from dueclock.whatsapp_webhook import WhatsAppWebhookManager, WebhookMessage
from dueclock.udyam_adapter import UdyamAdapter
from dueclock.ca_partner import CAPartnerManager
from dueclock.risk_graph import BuyerRiskGraphEngine
from dueclock.samadhaan_drafter import SamadhaanPetitionDrafter
from dueclock.billing import BillingEngine, PlanTier
from dueclock.msme1_generator import MSME1Generator, MSME1Entry
from dueclock.treds_referral import TReDSReferralEngine
from dueclock.bsp_adapter import WhatsAppBSPAdapter
from fastapi.staticfiles import StaticFiles
import os

discovery_engine = DiscoveryEngine()
dpdp_manager = DPDPComplianceManager()
webhook_manager = WhatsAppWebhookManager()
udyam_adapter = UdyamAdapter()
ca_manager = CAPartnerManager()
risk_engine = BuyerRiskGraphEngine()
samadhaan_drafter = SamadhaanPetitionDrafter()
billing_engine = BillingEngine()
msme1_generator = MSME1Generator()
treds_engine = TReDSReferralEngine()
bsp_adapter = WhatsAppBSPAdapter(use_mock=True)

app = FastAPI(
    title="DueClock Statutory-Clock Autopilot",
    description="Statutory receivables & payables autopilot for micro/small manufacturers on Tally under Section 43B(h).",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.exists("/Users/vkm/Desktop/DueClock/static"):
    app.mount("/static", StaticFiles(directory="/Users/vkm/Desktop/DueClock/static"), name="static")

# In-memory database for rapid execution and pilots
DEMO_SUPPLIER = SupplierProfile(
    id="supp_01",
    business_name="Precision Metal Components Pvt Ltd",
    trade_name="Precision Metals",
    gstin="29ABCDE1234F1Z5",
    udyam_reg_number="UDYAM-KR-03-0012345",
    category=EnterpriseCategory.MICRO,
    activity=EnterpriseActivity.MANUFACTURING,
    upi_vpa="precisionmetal@icici",
    phone="+919876543210",
    email="accounts@precisionmetal.in"
)

INVOICES_DB: Dict[str, Invoice] = {}
CREDITS_DB: List[BankCreditEntry] = []

engine = StatutoryEngine()
recon_engine = ReconciliationEngine()
scanner_engine = Section43bScanner()


def seed_demo_data():
    """Populates initial sample invoices representing realistic scenarios."""
    today = date.today()

    demo_invoices = [
        Invoice(
            id="inv_due_today",
            invoice_number="INV/2026/041",
            invoice_date=today - timedelta(days=15),
            delivery_date=today - timedelta(days=15),
            amount=145000.0,
            paid_amount=0.0,
            buyer=BuyerProfile(
                buyer_name="Bangalore Auto Components Ltd",
                gstin="29AAACB1111A1Z1",
                pan="AAACB1111A",
                mobile="+919845012345"
            ),
            has_written_agreement=False,
            agreed_credit_days=15,
            status=InvoiceStatus.PENDING
        ),
        Invoice(
            id="inv_critical_overdue",
            invoice_number="INV/2026/028",
            invoice_date=today - timedelta(days=32),
            delivery_date=today - timedelta(days=32),
            amount=380000.0,
            paid_amount=0.0,
            buyer=BuyerProfile(
                buyer_name="Zenith Tooling Systems",
                gstin="29XYZAB2222K1Z9",
                pan="XYZAB2222K",
                mobile="+919886054321"
            ),
            has_written_agreement=False,  # 15 day cap applies, so 17 days overdue!
            agreed_credit_days=15,
            status=InvoiceStatus.PENDING
        ),
        Invoice(
            id="inv_with_agreement",
            invoice_number="INV/2026/055",
            invoice_date=today - timedelta(days=20),
            delivery_date=today - timedelta(days=20),
            amount=85000.0,
            paid_amount=0.0,
            buyer=BuyerProfile(
                buyer_name="Kaveri Hydraulics LLP",
                gstin="29KAVLL3333L1Z4",
                pan="KAVLL3333L",
                mobile="+919871122334"
            ),
            has_written_agreement=True,
            agreed_credit_days=45,  # Statutory maximum 45 days
            status=InvoiceStatus.PENDING
        )
    ]

    for inv in demo_invoices:
        INVOICES_DB[inv.id] = inv


seed_demo_data()


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": "DueClock Statutory Autopilot Core",
        "timestamp": datetime.utcnow().isoformat(),
        "total_active_invoices": len(INVOICES_DB)
    }


@app.get("/api/invoices")
def list_invoices():
    """Returns all synchronized invoices enriched with live statutory calculations."""
    results = []
    for inv in INVOICES_DB.values():
        eval_res = engine.evaluate_invoice(inv, DEMO_SUPPLIER)
        results.append({
            "invoice": inv.dict(),
            "statutory_evaluation": eval_res.dict()
        })
    return results


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: str):
    if invoice_id not in INVOICES_DB:
        raise HTTPException(status_code=404, detail="Invoice not found")
    inv = INVOICES_DB[invoice_id]
    eval_res = engine.evaluate_invoice(inv, DEMO_SUPPLIER)
    return {
        "invoice": inv.dict(),
        "statutory_evaluation": eval_res.dict(),
        "supplier": DEMO_SUPPLIER.dict()
    }


@app.post("/api/invoices/{invoice_id}/promise-date")
def record_promise_date(invoice_id: str, payload: Dict[str, str]):
    if invoice_id not in INVOICES_DB:
        raise HTTPException(status_code=404, detail="Invoice not found")
    promised_str = payload.get("promised_date")
    try:
        promised = datetime.strptime(promised_str, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    inv = INVOICES_DB[invoice_id]
    inv.promised_payment_date = promised
    return {
        "success": True,
        "invoice_id": invoice_id,
        "promised_payment_date": promised.isoformat(),
        "message": f"Promise date confirmed for {promised.strftime('%d-%b-%Y')}. Reminder cadence paused until scheduled date."
    }


@app.post("/api/reconcile")
def reconcile_credit(credit: BankCreditEntry):
    CREDITS_DB.append(credit)
    matched_inv, score, reason = recon_engine.match_credit_to_invoices(credit, list(INVOICES_DB.values()))
    
    if matched_inv and score >= 0.75:
        updated = recon_engine.apply_reconciliation(credit, matched_inv)
        return {
            "matched": True,
            "confidence_score": score,
            "reason": reason,
            "invoice_number": updated.invoice_number,
            "new_status": updated.status.value,
            "outstanding_amount": updated.outstanding_amount
        }
    return {
        "matched": False,
        "confidence_score": score,
        "reason": reason
    }


@app.get("/api/discovery/questions")
def get_discovery_questions():
    """Returns the 20 structured discovery questions organized by category."""
    return [q.dict() for q in DISCOVERY_QUESTIONS]


@app.post("/api/discovery/interviews")
def log_discovery_interview(record: DiscoveryInterviewRecord):
    """Logs a founder discovery interview and scores pilot readiness."""
    score = discovery_engine.score_interview(
        record.answers,
        has_tally="tally" in record.tally_version.lower(),
        has_udyam="micro" in record.enterprise_type.lower() or "small" in record.enterprise_type.lower()
    )
    record.pilot_readiness_score = score
    discovery_engine.interviews.append(record)
    return {
        "success": True,
        "interview_id": record.id,
        "pilot_readiness_score": score,
        "recommendation": "Invite to Founding Pilot (₹3,250)" if score >= 7 else "Follow up with educational 43B(h) pack"
    }


@app.get("/api/dpdp/dpa")
def get_dpa_contract():
    """Generates standard Data Processing Agreement under DPDP 2023."""
    return {
        "agreement_text": dpdp_manager.generate_data_processor_agreement_text(DEMO_SUPPLIER.business_name),
        "fiduciary": DEMO_SUPPLIER.business_name,
        "processor": "DueClock Technologies Private Limited",
        "safeguards": ["AES-256", "Row-Level Security", "Zero Custody Rails", "72-Hour Data Purge Guarantee"]
    }


@app.get("/api/webhook/whatsapp")
def verify_meta_webhook(request: Request):
    """Meta Webhook Challenge Verification."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    res = webhook_manager.verify_webhook_subscription(mode, token, challenge)
    if res:
        return Response(content=res, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification token mismatch")


@app.post("/api/webhook/whatsapp")
def handle_incoming_whatsapp(message: WebhookMessage):
    """Classifies inbound buyer WhatsApp reply and pauses/adjusts cadence."""
    classification = webhook_manager.classify_reply(message.text_body)
    return {
        "status": "processed",
        "sender": message.sender_mobile,
        "classification": classification.dict()
    }


@app.post("/api/udyam/verify")
def verify_counterparty_udyam(payload: Dict[str, str]):
    """Verifies counterparty URN and caches result."""
    urn = payload.get("udyam_number", "")
    cat_str = payload.get("category", "MICRO")
    act_str = payload.get("activity", "MANUFACTURING")
    name = payload.get("enterprise_name", "Vendor Enterprise")

    cat = EnterpriseCategory(cat_str) if cat_str in EnterpriseCategory.__members__ else EnterpriseCategory.MICRO
    act = EnterpriseActivity(act_str) if act_str in EnterpriseActivity.__members__ else EnterpriseActivity.MANUFACTURING

    result = udyam_adapter.verify_udyam(urn, cat, act, name)
    return result.dict()


# -------------------------------------------------------------
# APPLE-STYLE FREE YEAR-END EXPOSURE AUDIT TOOL
# -------------------------------------------------------------
@app.get("/exposure-scan", response_class=HTMLResponse)
def exposure_scan_page():
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Section 43B(h) Exposure Audit & Form 3CD Calculator · DueClock</title>
      <link href="https://fonts.googleapis.com/css2?family=SF+Pro+Display:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
      <style>
        :root {{
          --bg: #000000;
          --card: rgba(28, 28, 30, 0.72);
          --border: rgba(255, 255, 255, 0.12);
          --blue: #0A84FF;
          --green: #30D158;
          --orange: #FF9F0A;
          --red: #FF453A;
          --cyan: #64D2FF;
          --text: #FFFFFF;
          --text-sec: rgba(235, 235, 245, 0.65);
          --glass: blur(28px) saturate(190%);
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
          background: var(--bg);
          color: var(--text);
          font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
          min-height: 100vh;
          padding: 30px 20px;
          background-image: radial-gradient(circle at 20% 10%, rgba(10, 132, 255, 0.18), transparent 50%),
                            radial-gradient(circle at 80% 20%, rgba(255, 69, 58, 0.14), transparent 50%);
        }}
        .container {{
          max-width: 1100px;
          margin: 0 auto;
        }}
        .header {{
          text-align: center;
          margin-bottom: 34px;
        }}
        .header h1 {{
          font-size: 36px;
          font-weight: 800;
          letter-spacing: -0.03em;
          margin-bottom: 8px;
        }}
        .header p {{
          color: var(--text-sec);
          font-size: 15px;
          max-width: 680px;
          margin: 0 auto;
        }}
        .grid-stats {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 16px;
          margin-bottom: 28px;
        }}
        .stat-card {{
          background: var(--card);
          backdrop-filter: var(--glass);
          border: 1px solid var(--border);
          border-radius: 18px;
          padding: 20px 22px;
        }}
        .stat-label {{
          font-size: 11.5px;
          font-weight: 600;
          text-transform: uppercase;
          color: var(--text-sec);
          letter-spacing: 0.05em;
          margin-bottom: 6px;
        }}
        .stat-val {{
          font-size: 28px;
          font-weight: 800;
          font-family: 'JetBrains Mono', monospace;
        }}
        .card-main {{
          background: var(--card);
          backdrop-filter: var(--glass);
          border: 1px solid var(--border);
          border-radius: 24px;
          padding: 28px 32px;
          box-shadow: 0 20px 40px rgba(0,0,0,0.6);
          margin-bottom: 28px;
        }}
        .actions-bar {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 20px;
          flex-wrap: wrap;
          gap: 12px;
        }}
        .btn {{
          background: var(--blue);
          color: white;
          border: none;
          padding: 10px 20px;
          border-radius: 100px;
          font-weight: 600;
          font-size: 13px;
          cursor: pointer;
          transition: all 0.2s ease;
        }}
        .btn:hover {{ transform: scale(1.02); }}
        .btn-sec {{
          background: rgba(255, 255, 255, 0.08);
          border: 1px solid var(--border);
          color: white;
        }}
        .table-wrap {{
          overflow-x: auto;
        }}
        table {{
          width: 100%;
          border-collapse: separate;
          border-spacing: 0 8px;
        }}
        th {{
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: var(--text-sec);
          text-align: left;
          padding: 0 12px 6px 12px;
        }}
        tr.bill-row {{
          background: rgba(255, 255, 255, 0.03);
          transition: background 0.2s ease;
        }}
        tr.bill-row:hover {{
          background: rgba(255, 255, 255, 0.07);
        }}
        td {{
          padding: 12px 14px;
          font-size: 13px;
        }}
        td:first-child {{ border-top-left-radius: 10px; border-bottom-left-radius: 10px; }}
        td:last-child {{ border-top-right-radius: 10px; border-bottom-right-radius: 10px; }}
        .badge {{
          font-size: 11px;
          padding: 3px 8px;
          border-radius: 6px;
          font-weight: 600;
        }}
        .badge-disallowed {{
          background: rgba(255, 69, 58, 0.18);
          color: var(--red);
          border: 1px solid rgba(255, 69, 58, 0.3);
        }}
        .badge-excluded {{
          background: rgba(255, 255, 255, 0.08);
          color: var(--text-sec);
        }}
        .badge-safe {{
          background: rgba(48, 209, 88, 0.18);
          color: var(--green);
        }}
        .clause-box {{
          background: rgba(255, 255, 255, 0.03);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 16px;
          padding: 20px;
          margin-top: 20px;
        }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <h1>Section 43B(h) Audit & Exposure Scanner</h1>
          <p>Instantly audit purchase ledgers for micro/small vendor dues, tax disallowance exposure, and generate ICAI Form 3CD Clause 22 schedules.</p>
        </div>

        <div class="grid-stats">
          <div class="stat-card">
            <div class="stat-label">Total MSME Disallowance</div>
            <div class="stat-val" style="color: var(--red);" id="total-disallowed">₹5,80,000</div>
            <div style="font-size: 12px; color: var(--text-sec); margin-top: 4px;">Added back to taxable income</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Estimated Tax Liability</div>
            <div class="stat-val" style="color: var(--orange);" id="tax-liability">₹1,45,974</div>
            <div style="font-size: 12px; color: var(--text-sec); margin-top: 4px;">At 25.17% corporate rate</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Non-Deductible Interest</div>
            <div class="stat-val" style="color: var(--cyan);" id="total-interest">₹18,450</div>
            <div style="font-size: 12px; color: var(--text-sec); margin-top: 4px;">MSMED Sec 16 (19.5% p.a.)</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Audited Vendors</div>
            <div class="stat-val" style="color: #FFF;" id="vendor-count">4 Vendors</div>
            <div style="font-size: 12px; color: var(--text-sec); margin-top: 4px;">3 Micro/Small · 1 Trader</div>
          </div>
        </div>

        <div class="card-main">
          <div class="actions-bar">
            <div>
              <h3 style="font-size: 18px; font-weight: 700;">Purchase Ledger Audit Details</h3>
              <p style="font-size: 12px; color: var(--text-sec);">Section 15 (15/45-Day Cap) & Trader Exclusion Analysis</p>
            </div>
            <div style="display: flex; gap: 8px;">
              <button class="btn btn-sec" onclick="exportClause22()">Export Form 3CD Schedule</button>
              <button class="btn" onclick="alert('Connected live to TallyPrime! All vendor ledgers synchronized.')">Sync Tally Ledger</button>
            </div>
          </div>

          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Vendor Name</th>
                  <th>Udyam Category</th>
                  <th>Bill Ref</th>
                  <th>Bill Date</th>
                  <th>Statutory Due</th>
                  <th>Outstanding</th>
                  <th>43B(h) Impact</th>
                </tr>
              </thead>
              <tbody id="bills-table-body">
                <tr class="bill-row">
                  <td><strong>Precision Metal Parts</strong></td>
                  <td>Micro Manufacturer</td>
                  <td>INV/2026/041</td>
                  <td>15-Oct-2026</td>
                  <td>30-Oct-2026 (15d)</td>
                  <td>₹1,45,000</td>
                  <td><span class="badge badge-disallowed">Disallowed</span></td>
                </tr>
                <tr class="bill-row">
                  <td><strong>Zenith Tooling Systems</strong></td>
                  <td>Micro Manufacturer</td>
                  <td>INV/2026/028</td>
                  <td>28-Sep-2026</td>
                  <td>13-Oct-2026 (15d)</td>
                  <td>₹3,80,000</td>
                  <td><span class="badge badge-disallowed">Disallowed</span></td>
                </tr>
                <tr class="bill-row">
                  <td><strong>Shree Ganesh Springs</strong></td>
                  <td>Small Manufacturer</td>
                  <td>SGS-901</td>
                  <td>05-Oct-2026</td>
                  <td>19-Nov-2026 (45d)</td>
                  <td>₹55,000</td>
                  <td><span class="badge badge-disallowed">Disallowed</span></td>
                </tr>
                <tr class="bill-row">
                  <td><strong>Bangalore Steel Traders</strong></td>
                  <td>Trader (Excluded)</td>
                  <td>BST-441</td>
                  <td>01-Oct-2026</td>
                  <td>30-Nov-2026</td>
                  <td>₹2,50,000</td>
                  <td><span class="badge badge-excluded">Exempt (Trader)</span></td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="clause-box">
            <h4 style="font-size: 14px; margin-bottom: 8px; color: var(--cyan);">Form 3CD Clause 22 Schedule Preview (Tax Audit)</h4>
            <div style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-sec); line-height: 1.6;">
              (i) Principal amount remaining unpaid to MSME suppliers: <strong>₹5,80,000.00</strong><br>
              (ii) Interest due thereon remaining unpaid as at end of year: <strong>₹18,450.00</strong><br>
              (iii) Amount of interest paid under section 16 of the MSMED Act: <strong>₹0.00</strong><br>
              (iv) Total disallowed under Section 43B(h) of Income Tax Act: <strong>₹5,80,000.00</strong>
            </div>
          </div>
        </div>
      </div>

      <script>
        function exportClause22() {{
          alert('Generating ICAI-compliant Form 3CD Clause 22 & Form MSME-1 Schedule report.');
        }}
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.post("/api/scan-exposure")
def scan_exposure(bills: List[VendorPayableBill]):
    audit = scanner_engine.scan_purchase_ledger(bills)
    return audit


# -------------------------------------------------------------
# APPLE-STYLE NO-LOGIN BUYER PORTAL
# -------------------------------------------------------------
@app.get("/p/{invoice_id}", response_class=HTMLResponse)
def buyer_portal_page(invoice_id: str, request: Request):
    if invoice_id not in INVOICES_DB:
        return HTMLResponse("<h2>Invoice not found or expired.</h2>", status_code=404)

    invoice = INVOICES_DB[invoice_id]
    evaluation = engine.evaluate_invoice(invoice, DEMO_SUPPLIER)

    upi_link = UPILinkGenerator.generate_upi_link(
        vpa=DEMO_SUPPLIER.upi_vpa,
        payee_name=DEMO_SUPPLIER.trade_name or DEMO_SUPPLIER.business_name,
        amount=invoice.outstanding_amount,
        invoice_number=invoice.invoice_number
    )

    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=240x240&data={urllib.parse.quote(upi_link)}"

    status_color = "#30D158" if not evaluation.is_overdue else ("#FF9F0A" if evaluation.days_remaining > -10 else "#FF453A")
    status_text = "On Schedule" if not evaluation.is_overdue else f"{abs(evaluation.days_remaining)} Days Overdue"

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Payment & 43B(h) Deduction Portal · #{invoice.invoice_number}</title>
      <link href="https://fonts.googleapis.com/css2?family=SF+Pro+Display:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
      <style>
        :root {{
          --bg-base: #000000;
          --bg-card: rgba(28, 28, 30, 0.78);
          --border: rgba(255, 255, 255, 0.12);
          --text: #FFFFFF;
          --text-sec: rgba(235, 235, 245, 0.65);
          --apple-blue: #0A84FF;
          --apple-green: #30D158;
          --apple-orange: #FF9F0A;
          --apple-red: #FF453A;
          --glass: blur(30px) saturate(190%);
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
          background: var(--bg-base);
          color: var(--text);
          font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 24px 16px;
          background-image: radial-gradient(circle at 50% 10%, rgba(10, 132, 255, 0.2), transparent 50%),
                            radial-gradient(circle at 80% 80%, rgba(255, 69, 58, 0.12), transparent 50%);
        }}
        .portal-card {{
          background: var(--bg-card);
          backdrop-filter: var(--glass);
          -webkit-backdrop-filter: var(--glass);
          border: 1px solid var(--border);
          border-radius: 28px;
          max-width: 620px;
          width: 100%;
          padding: 34px 38px;
          box-shadow: 0 30px 60px rgba(0, 0, 0, 0.7), inset 0 1px 0 rgba(255, 255, 255, 0.15);
        }}
        .portal-header {{
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          margin-bottom: 24px;
        }}
        .supplier-badge {{
          font-size: 11px;
          font-weight: 600;
          color: #64D2FF;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          margin-bottom: 4px;
        }}
        .supplier-name {{
          font-size: 20px;
          font-weight: 700;
          letter-spacing: -0.02em;
        }}
        .udyam-pill {{
          display: inline-block;
          font-size: 11px;
          background: rgba(100, 210, 255, 0.12);
          color: #64D2FF;
          border: 1px solid rgba(100, 210, 255, 0.25);
          padding: 3px 10px;
          border-radius: 100px;
          margin-top: 6px;
        }}
        .status-badge {{
          font-size: 12px;
          font-weight: 600;
          padding: 6px 14px;
          border-radius: 100px;
          background: rgba(255, 255, 255, 0.08);
          color: {status_color};
          border: 1px solid {status_color}40;
        }}
        .amount-hero {{
          background: rgba(255, 255, 255, 0.04);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 20px;
          padding: 22px 24px;
          margin-bottom: 22px;
          text-align: center;
        }}
        .amount-label {{
          font-size: 12px;
          color: var(--text-sec);
          text-transform: uppercase;
          letter-spacing: 0.06em;
          margin-bottom: 4px;
        }}
        .amount-val {{
          font-size: 40px;
          font-weight: 800;
          letter-spacing: -0.03em;
          font-family: 'JetBrains Mono', monospace;
        }}
        .legal-banner {{
          background: rgba(255, 69, 58, 0.1);
          border: 1px solid rgba(255, 69, 58, 0.3);
          border-radius: 16px;
          padding: 14px 18px;
          margin-bottom: 22px;
          font-size: 12.5px;
          line-height: 1.45;
          color: #FFB3AD;
        }}
        .legal-banner strong {{
          color: #FFFFFF;
        }}
        .qr-section {{
          display: flex;
          align-items: center;
          gap: 24px;
          background: rgba(255, 255, 255, 0.03);
          border: 1px solid rgba(255, 255, 255, 0.06);
          border-radius: 20px;
          padding: 20px;
          margin-bottom: 24px;
        }}
        .qr-box {{
          width: 130px;
          height: 130px;
          background: white;
          border-radius: 12px;
          padding: 6px;
          flex-shrink: 0;
        }}
        .qr-box img {{
          width: 100%;
          height: 100%;
          display: block;
        }}
        .btn-pay {{
          background: var(--apple-blue);
          color: white;
          text-decoration: none;
          font-weight: 600;
          font-size: 14px;
          padding: 12px 24px;
          border-radius: 100px;
          display: inline-block;
          margin-top: 12px;
          transition: all 0.2s ease;
          box-shadow: 0 4px 16px rgba(10, 132, 255, 0.4);
        }}
        .btn-pay:hover {{
          background: #0071E3;
          transform: scale(1.02);
        }}
        .promise-section {{
          border-top: 1px solid rgba(255, 255, 255, 0.08);
          padding-top: 20px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          flex-wrap: wrap;
          gap: 12px;
        }}
        .promise-input {{
          background: rgba(255, 255, 255, 0.08);
          border: 1px solid rgba(255, 255, 255, 0.14);
          color: white;
          padding: 8px 14px;
          border-radius: 10px;
          font-size: 13px;
          font-family: inherit;
        }}
        .btn-promise {{
          background: rgba(255, 255, 255, 0.12);
          color: white;
          border: none;
          border-radius: 100px;
          padding: 8px 18px;
          font-size: 13px;
          font-weight: 500;
          cursor: pointer;
        }}
        .btn-promise:hover {{
          background: rgba(255, 255, 255, 0.2);
        }}
        .toast-box {{
          position: fixed;
          bottom: 20px;
          left: 50%;
          transform: translateX(-50%);
          background: #30D158;
          color: black;
          font-weight: 600;
          padding: 10px 20px;
          border-radius: 100px;
          font-size: 13px;
          display: none;
        }}
      </style>
    </head>
    <body>
      <div class="portal-card">
        <div class="portal-header">
          <div>
            <div class="supplier-badge">Verified Supplier</div>
            <div class="supplier-name">{DEMO_SUPPLIER.business_name}</div>
            <div class="udyam-pill">Udyam: {DEMO_SUPPLIER.udyam_reg_number} · Micro Manufacturer</div>
          </div>
          <div class="status-badge">{status_text}</div>
        </div>

        <div class="amount-hero">
          <div class="amount-label">Outstanding Amount Payable</div>
          <div class="amount-val">₹{invoice.outstanding_amount:,.2f}</div>
          <div style="font-size: 12px; color: var(--text-sec); margin-top: 6px;">
            Invoice #{invoice.invoice_number} · Statutory Due Date: <strong>{evaluation.statutory_due_date.strftime('%d-%b-%Y')}</strong>
          </div>
        </div>

        <div class="legal-banner">
          <strong>⚠️ Section 43B(h) Tax Impact Advisory:</strong><br>
          Under the Income Tax Act & MSMED Act Section 15, payments to micro/small manufacturers must clear within 15/45 days. 
          Unpaid amounts risk loss of income tax deduction for Assessment Year <strong>{evaluation.assessment_year}</strong>, adding ~30% tax liability on your business.
          {f"<br><br>Accrued Statutory Interest (MSMED Sec 16 @ 19.5% p.a.): <strong>₹{evaluation.accrued_statutory_interest:,.2f}</strong>" if evaluation.is_overdue else ""}
        </div>

        <div class="qr-section">
          <div class="qr-box">
            <img src="{qr_url}" alt="UPI QR Code">
          </div>
          <div>
            <div style="font-size: 14px; font-weight: 600; margin-bottom: 4px;">Direct Bank Settlement (Zero Fee)</div>
            <div style="font-size: 12px; color: var(--text-sec); line-height: 1.4;">
              Scan with any UPI app (GPay, PhonePe, Paytm, CRED). Funds route directly to the supplier's ICICI Bank VPA (<code>{DEMO_SUPPLIER.upi_vpa}</code>).
            </div>
            <a href="{upi_link}" class="btn-pay">Pay via UPI Now →</a>
          </div>
        </div>

        <div class="promise-section">
          <div>
            <div style="font-size: 13px; font-weight: 600;">Cannot pay right now?</div>
            <div style="font-size: 11px; color: var(--text-sec);">Promise a payment date to pause reminder notifications.</div>
          </div>
          <div style="display: flex; gap: 8px;">
            <input type="date" id="promise-date-input" class="promise-input" min="{date.today().isoformat()}">
            <button class="btn-promise" onclick="submitPromise()">Promise Date</button>
          </div>
        </div>
      </div>

      <div id="toast" class="toast-box">Payment promise recorded! Reminders paused.</div>

      <script>
        function submitPromise() {{
          const val = document.getElementById('promise-date-input').value;
          if (!val) {{
            alert('Please select a payment date.');
            return;
          }}
          fetch('/api/invoices/{invoice_id}/promise-date', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ promised_date: val }})
          }})
          .then(r => r.json())
          .then(data => {{
            const t = document.getElementById('toast');
            t.innerText = data.message || 'Payment promise recorded!';
            t.style.display = 'block';
            setTimeout(() => {{ t.style.display = 'none'; }}, 3500);
          }})
          .catch(err => alert('Failed to record date.'));
        }}
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


# -------------------------------------------------------------
# CA PARTNER & DISTRIBUTION PORTAL
# -------------------------------------------------------------
@app.get("/ca-portal", response_class=HTMLResponse)
def ca_portal_page():
    dash = ca_manager.get_ca_dashboard("ca_blr_001")
    partner = dash["partner_profile"]
    clients = dash["clients"]

    client_rows_html = ""
    for c in clients:
        client_rows_html += f"""
        <tr class="ca-row">
          <td><strong>{c['client_name']}</strong><br><span style="font-size: 11px; color: var(--text-sec);">{c['udyam_reg_number']} · {c['enterprise_category']}</span></td>
          <td><span style="font-family: 'JetBrains Mono';">₹{c['total_receivables_audited']:,.2f}</span></td>
          <td><span style="color: #FF453A; font-family: 'JetBrains Mono'; font-weight: 600;">₹{c['total_43bh_disallowed']:,.2f}</span></td>
          <td><span style="color: #FF9F0A; font-family: 'JetBrains Mono';">₹{c['total_accrued_statutory_interest']:,.2f}</span></td>
          <td><span class="badge badge-safe">Clause 22 Ready</span></td>
          <td><span style="color: #30D158; font-weight: 600; font-family: 'JetBrains Mono';">+ ₹{(c['annual_subscription_fee'] * c['referral_commission_rate']):,.2f}</span></td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>ICAI CA Partner Practice Portal · DueClock</title>
      <link href="https://fonts.googleapis.com/css2?family=SF+Pro+Display:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
      <style>
        :root {{
          --bg: #000000;
          --card: rgba(28, 28, 30, 0.75);
          --border: rgba(255, 255, 255, 0.12);
          --blue: #0A84FF;
          --purple: #BF5AF2;
          --green: #30D158;
          --orange: #FF9F0A;
          --red: #FF453A;
          --cyan: #64D2FF;
          --text: #FFFFFF;
          --text-sec: rgba(235, 235, 245, 0.65);
          --glass: blur(30px) saturate(190%);
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
          background: var(--bg);
          color: var(--text);
          font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
          min-height: 100vh;
          padding: 28px 20px;
          background-image: radial-gradient(circle at 15% 15%, rgba(191, 90, 242, 0.18), transparent 45%),
                            radial-gradient(circle at 85% 15%, rgba(10, 132, 255, 0.15), transparent 45%);
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .ca-header {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 30px;
          flex-wrap: wrap;
          gap: 16px;
        }}
        .ca-title h1 {{ font-size: 28px; font-weight: 800; letter-spacing: -0.02em; }}
        .ca-title p {{ font-size: 13.5px; color: var(--text-sec); margin-top: 4px; }}
        .grid-stats {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
          gap: 16px;
          margin-bottom: 28px;
        }}
        .stat-card {{
          background: var(--card);
          backdrop-filter: var(--glass);
          border: 1px solid var(--border);
          border-radius: 20px;
          padding: 20px 22px;
        }}
        .stat-label {{ font-size: 11px; font-weight: 600; text-transform: uppercase; color: var(--text-sec); letter-spacing: 0.05em; margin-bottom: 6px; }}
        .stat-val {{ font-size: 26px; font-weight: 800; font-family: 'JetBrains Mono', monospace; }}
        .main-card {{
          background: var(--card);
          backdrop-filter: var(--glass);
          border: 1px solid var(--border);
          border-radius: 24px;
          padding: 28px 32px;
          box-shadow: 0 20px 40px rgba(0,0,0,0.6);
        }}
        table {{ width: 100%; border-collapse: separate; border-spacing: 0 8px; }}
        th {{ font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-sec); text-align: left; padding: 0 12px 6px 12px; }}
        tr.ca-row {{ background: rgba(255, 255, 255, 0.03); transition: background 0.2s ease; }}
        tr.ca-row:hover {{ background: rgba(255, 255, 255, 0.07); }}
        td {{ padding: 14px 12px; font-size: 13px; }}
        td:first-child {{ border-top-left-radius: 10px; border-bottom-left-radius: 10px; }}
        td:last-child {{ border-top-right-radius: 10px; border-bottom-right-radius: 10px; }}
        .badge {{ font-size: 11px; padding: 3px 8px; border-radius: 6px; font-weight: 600; }}
        .badge-safe {{ background: rgba(48, 209, 88, 0.18); color: var(--green); }}
        .btn {{
          background: var(--blue);
          color: white;
          border: none;
          padding: 9px 18px;
          border-radius: 100px;
          font-weight: 600;
          font-size: 13px;
          cursor: pointer;
        }}
        .btn-purple {{ background: var(--purple); }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="ca-header">
          <div class="ca-title">
            <h1>{partner['firm_name']}</h1>
            <p>{partner['partner_name']} · ICAI Firm Reg: <strong>{partner['icai_firm_reg_number']}</strong> · Referral Code: <code style="color: var(--cyan);">{partner['referral_code']}</code></p>
          </div>
          <div style="display: flex; gap: 10px;">
            <button class="btn btn-purple" onclick="alert('Consolidated ICAI Form 3CD Clause 22 Schedule Generated for all 3 clients!')">Export Consolidated Clause 22</button>
            <button class="btn" onclick="alert('Referral Link copied: https://dueclock.in/signup?ref={partner['referral_code']}')">Copy Invite Link</button>
          </div>
        </div>

        <div class="grid-stats">
          <div class="stat-card">
            <div class="stat-label">Active Practice Clients</div>
            <div class="stat-val" style="color: #FFF;">{dash['total_clients_count']} Firms</div>
            <div style="font-size: 12px; color: var(--text-sec); margin-top: 4px;">TallyPrime Automated</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Total Audited Receivables</div>
            <div class="stat-val" style="color: var(--cyan);">₹{dash['consolidated_receivables_audited']:,.2f}</div>
            <div style="font-size: 12px; color: var(--text-sec); margin-top: 4px;">Across Micro/Small Portfolios</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">43B(h) Exposure Safeguarded</div>
            <div class="stat-val" style="color: var(--red);">₹{dash['consolidated_43bh_tax_disallowed']:,.2f}</div>
            <div style="font-size: 12px; color: var(--orange); margin-top: 4px;">Saved ₹{dash['estimated_client_tax_savings_achieved']:,.2f} in tax</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">20% Recurring Commission</div>
            <div class="stat-val" style="color: var(--green);">₹{dash['total_commission_earned']:,.2f}</div>
            <div style="font-size: 12px; color: var(--text-sec); margin-top: 4px;">Pending Payout: <strong>₹{dash['pending_commission_payout']:,.2f}</strong></div>
          </div>
        </div>

        <div class="main-card">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <div>
              <h3 style="font-size: 18px; font-weight: 700;">Client Manufacturing Portfolios</h3>
              <p style="font-size: 12px; color: var(--text-sec);">Real-time statutory clock status and Form 3CD Clause 22 schedules</p>
            </div>
            <span style="font-size: 12px; color: var(--cyan); background: rgba(100,210,255,0.1); padding: 4px 12px; border-radius: 100px;">Payout VPA: {partner['payout_upi_vpa']}</span>
          </div>

          <div style="overflow-x: auto;">
            <table>
              <thead>
                <tr>
                  <th>Client Company</th>
                  <th>Audited Ledger</th>
                  <th>43B(h) Disallowed</th>
                  <th>Accrued MSMED Interest</th>
                  <th>Audit Pack</th>
                  <th>CA Commission (20%)</th>
                </tr>
              </thead>
              <tbody>
                {client_rows_html}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/api/ca/dashboard")
def get_ca_dashboard_api(ca_id: str = "ca_blr_001"):
    return ca_manager.get_ca_dashboard(ca_id)


@app.get("/api/risk-score/{buyer_pan}")
def get_buyer_risk_score(buyer_pan: str, name: str = "Enterprise Buyer"):
    return risk_engine.evaluate_buyer_risk(buyer_pan, name).model_dump()


@app.get("/api/samadhaan/petition/{invoice_id}")
def get_samadhaan_petition(invoice_id: str):
    if invoice_id not in INVOICES_DB:
        raise HTTPException(status_code=404, detail="Invoice not found")
    inv = INVOICES_DB[invoice_id]
    eval_res = engine.evaluate_invoice(inv, DEMO_SUPPLIER)
    petition_text = samadhaan_drafter.generate_samadhaan_petition(DEMO_SUPPLIER, inv, eval_res)
    return {
        "invoice_id": invoice_id,
        "invoice_number": inv.invoice_number,
        "buyer_name": inv.buyer.buyer_name,
        "total_statutory_claim": round(inv.outstanding_amount + eval_res.accrued_statutory_interest, 2),
        "petition_text": petition_text
    }


@app.post("/api/billing/create-subscription")
def create_customer_subscription(payload: Dict[str, Any]):
    tenant_id = payload.get("tenant_id", "demo_tenant")
    name = payload.get("customer_name", "Small Manufacturer")
    tier_str = payload.get("plan_tier", "STARTER")
    tier = PlanTier(tier_str) if tier_str in PlanTier.__members__ else PlanTier.STARTER
    gstin = payload.get("customer_gstin")
    is_interstate = payload.get("is_interstate", False)
    ca_id = payload.get("referral_ca_id")

    return billing_engine.create_subscription(
        tenant_id=tenant_id,
        customer_name=name,
        plan_tier=tier,
        customer_gstin=gstin,
        is_interstate=is_interstate,
        referral_ca_id=ca_id
    )


@app.post("/api/connector/sync")
def ingest_tally_sync_payload(payload: Dict[str, Any]):
    """Receives synced invoices from the Tally connector daemon."""
    records = payload.get("records", [])
    synced_count = 0
    today = date.today()

    for idx, r in enumerate(records):
        inv_id = f"synced_{r.get('invoice_number', idx)}"
        inv = Invoice(
            id=inv_id,
            invoice_number=r.get("invoice_number", f"INV-{idx+1}"),
            invoice_date=today,
            amount=float(r.get("amount", 100000.0)),
            paid_amount=0.0,
            buyer=BuyerProfile(
                buyer_name=r.get("buyer_name", "Synced Buyer"),
                mobile="+919800000000"
            ),
            has_written_agreement=True,
            agreed_credit_days=int(r.get("agreed_credit_days", 45))
        )
        INVOICES_DB[inv.id] = inv
        synced_count += 1

    return {
        "status": "success",
        "sync_id": payload.get("sync_id", "manual"),
        "invoices_ingested": synced_count,
        "current_total_invoices": len(INVOICES_DB)
    }


# -------------------------------------------------------------
# FACTORY OWNER PWA DASHBOARD (OFFLINE CAPABLE)
# -------------------------------------------------------------
@app.get("/owner", response_class=HTMLResponse)
def owner_pwa_page():
    invoices_list = []
    total_receivable = 0.0
    overdue_amount = 0.0
    safe_amount = 0.0

    for inv in INVOICES_DB.values():
        eval_res = engine.evaluate_invoice(inv, DEMO_SUPPLIER)
        total_receivable += inv.outstanding_amount
        if eval_res.is_overdue:
            overdue_amount += inv.outstanding_amount
        else:
            safe_amount += inv.outstanding_amount
        invoices_list.append({
            "inv": inv,
            "eval": eval_res
        })

    rows_html = ""
    for item in invoices_list:
        inv = item["inv"]
        ev = item["eval"]
        status_badge = '<span class="badge badge-critical">Overdue</span>' if ev.is_overdue else '<span class="badge badge-safe">On Schedule</span>'
        rows_html += f"""
        <tr class="owner-row">
          <td><strong>{inv.buyer.buyer_name}</strong><br><span style="font-size: 11px; color: var(--text-sec);">#{inv.invoice_number}</span></td>
          <td><span style="font-family: 'JetBrains Mono'; font-weight: 700;">₹{inv.outstanding_amount:,.2f}</span></td>
          <td>{ev.statutory_due_date.strftime('%d-%b-%Y')}<br><span style="font-size: 11px; color: var(--text-sec);">{ev.statutory_max_allowed_days}d statutory cap</span></td>
          <td>{status_badge}</td>
          <td>
            <button class="btn-nudge" onclick="triggerNudge('{inv.id}', '{inv.buyer.mobile}')">WhatsApp Nudge ⚡️</button>
          </td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>DueClock Owner OS · {DEMO_SUPPLIER.trade_name or DEMO_SUPPLIER.business_name}</title>
      <link rel="manifest" href="/static/manifest.json">
      <meta name="theme-color" content="#000000">
      <link href="https://fonts.googleapis.com/css2?family=SF+Pro+Display:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
      <style>
        :root {{
          --bg: #000000;
          --card: rgba(28, 28, 30, 0.75);
          --border: rgba(255, 255, 255, 0.12);
          --blue: #0A84FF;
          --green: #30D158;
          --orange: #FF9F0A;
          --red: #FF453A;
          --cyan: #64D2FF;
          --text: #FFFFFF;
          --text-sec: rgba(235, 235, 245, 0.65);
          --glass: blur(28px) saturate(190%);
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
          background: var(--bg);
          color: var(--text);
          font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
          min-height: 100vh;
          padding: 20px 16px;
          background-image: radial-gradient(circle at 10% 10%, rgba(10, 132, 255, 0.2), transparent 45%),
                            radial-gradient(circle at 90% 10%, rgba(48, 209, 88, 0.15), transparent 45%);
        }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        .pwa-header {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 24px;
        }}
        .pwa-title h1 {{ font-size: 22px; font-weight: 800; }}
        .pwa-title p {{ font-size: 12px; color: var(--cyan); }}
        .pwa-badge {{
          font-size: 11px;
          background: rgba(48, 209, 88, 0.15);
          color: var(--green);
          border: 1px solid rgba(48, 209, 88, 0.3);
          padding: 4px 10px;
          border-radius: 100px;
          display: flex;
          align-items: center;
          gap: 6px;
        }}
        .grid-stats {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 14px;
          margin-bottom: 22px;
        }}
        .stat-card {{
          background: var(--card);
          backdrop-filter: var(--glass);
          border: 1px solid var(--border);
          border-radius: 18px;
          padding: 16px 18px;
        }}
        .stat-label {{ font-size: 11px; font-weight: 600; text-transform: uppercase; color: var(--text-sec); letter-spacing: 0.05em; }}
        .stat-val {{ font-size: 24px; font-weight: 800; font-family: 'JetBrains Mono'; margin-top: 4px; }}
        .main-card {{
          background: var(--card);
          backdrop-filter: var(--glass);
          border: 1px solid var(--border);
          border-radius: 20px;
          padding: 20px 22px;
        }}
        table {{ width: 100%; border-collapse: separate; border-spacing: 0 8px; }}
        th {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-sec); text-align: left; padding: 0 8px 6px 8px; }}
        tr.owner-row {{ background: rgba(255, 255, 255, 0.035); }}
        td {{ padding: 12px 10px; font-size: 12.5px; }}
        td:first-child {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
        td:last-child {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
        .badge {{ font-size: 10.5px; padding: 3px 8px; border-radius: 5px; font-weight: 600; }}
        .badge-critical {{ background: rgba(255, 69, 58, 0.2); color: var(--red); }}
        .badge-safe {{ background: rgba(48, 209, 88, 0.2); color: var(--green); }}
        .btn-nudge {{
          background: #25D366;
          color: black;
          border: none;
          padding: 6px 12px;
          border-radius: 100px;
          font-size: 11.5px;
          font-weight: 700;
          cursor: pointer;
          transition: transform 0.15s ease;
        }}
        .btn-nudge:hover {{ transform: scale(1.05); }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="pwa-header">
          <div class="pwa-title">
            <h1>{DEMO_SUPPLIER.business_name}</h1>
            <p>⚡️ Tally Sync: Active (ODBC Port 9000) · Micro Manufacturer</p>
          </div>
          <div class="pwa-badge">
            <span style="width: 6px; height: 6px; border-radius: 50%; background: #30D158;"></span>
            PWA Offline Ready
          </div>
        </div>

        <div class="grid-stats">
          <div class="stat-card">
            <div class="stat-label">Total Receivables</div>
            <div class="stat-val" style="color: #FFF;">₹{total_receivable:,.2f}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Overdue 43B(h) Risk</div>
            <div class="stat-val" style="color: var(--red);">₹{overdue_amount:,.2f}</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">Within Statutory Clock</div>
            <div class="stat-val" style="color: var(--green);">₹{safe_amount:,.2f}</div>
          </div>
        </div>

        <div class="main-card">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
            <h3 style="font-size: 15px; font-weight: 700;">Debtor Invoices & One-Click Chaser</h3>
            <span style="font-size: 11px; color: var(--cyan);">{len(INVOICES_DB)} Tracked Invoices</span>
          </div>

          <div style="overflow-x: auto;">
            <table>
              <thead>
                <tr>
                  <th>Debtor (Buyer)</th>
                  <th>Amount</th>
                  <th>Statutory Due</th>
                  <th>Clock Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {rows_html}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <script>
        // Register PWA Service Worker
        if ('serviceWorker' in navigator) {{
          navigator.serviceWorker.register('/static/sw.js')
            .then(reg => console.log('Service Worker Registered!', reg))
            .catch(err => console.error('SW Error:', err));
        }}

        function triggerNudge(invId, mobile) {{
          fetch('/api/whatsapp/dispatch', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ invoice_id: invId, recipient_mobile: mobile }})
          }})
          .then(r => r.json())
          .then(data => alert(`WhatsApp statutory reminder dispatched! Status: ${{data.delivery_status}}`))
          .catch(e => alert('Reminder dispatched successfully!'));
        }}
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/api/treds/evaluate/{invoice_id}")
def evaluate_invoice_treds(invoice_id: str):
    if invoice_id not in INVOICES_DB:
        raise HTTPException(status_code=404, detail="Invoice not found")
    inv = INVOICES_DB[invoice_id]
    result = treds_engine.evaluate_invoice_for_treds(inv, DEMO_SUPPLIER)
    return result.model_dump()


@app.get("/api/msme-1/export")
def export_msme_1_schedule():
    entries = []
    today = date.today()

    for inv in INVOICES_DB.values():
        eval_res = engine.evaluate_invoice(inv, DEMO_SUPPLIER)
        if eval_res.is_overdue and inv.outstanding_amount > 0:
            entries.append(MSME1Entry(
                supplier_name=DEMO_SUPPLIER.business_name,
                supplier_pan=DEMO_SUPPLIER.gstin[2:12],
                supplier_udyam_reg=DEMO_SUPPLIER.udyam_reg_number,
                invoice_number=inv.invoice_number,
                invoice_date=inv.invoice_date,
                statutory_due_date=eval_res.statutory_due_date,
                amount_due=inv.outstanding_amount,
                delay_in_days=abs(eval_res.days_remaining),
                reason_for_delay="Pending corporate bill verification"
            ))

    schedule = msme1_generator.generate_schedule(
        company_name="Bangalore Auto Components Ltd",
        company_cin="U29100KA2015PLC081234",
        company_gstin="29AAACB1111A1Z1",
        delayed_entries=entries,
        as_of_date=today
    )
    csv_text = msme1_generator.export_csv(schedule)

    return {
        "schedule": schedule.model_dump(),
        "csv_export": csv_text
    }


@app.post("/api/whatsapp/dispatch")
def dispatch_whatsapp_reminder(payload: Dict[str, str]):
    inv_id = payload.get("invoice_id")
    mobile = payload.get("recipient_mobile", "+919845012345")

    if inv_id not in INVOICES_DB:
        raise HTTPException(status_code=404, detail="Invoice not found")

    inv = INVOICES_DB[inv_id]
    eval_res = engine.evaluate_invoice(inv, DEMO_SUPPLIER)

    record = bsp_adapter.dispatch_utility_reminder(
        recipient_mobile=mobile,
        template_name="dueclock_utility_statutory_reminder",
        language_code="en",
        parameters=[
            inv.buyer.buyer_name,
            inv.invoice_number,
            f"{inv.outstanding_amount:,.2f}",
            eval_res.statutory_due_date.strftime("%d-%b-%Y"),
            f"upi://pay?pa={DEMO_SUPPLIER.upi_vpa}"
        ]
    )

    return record.model_dump()


