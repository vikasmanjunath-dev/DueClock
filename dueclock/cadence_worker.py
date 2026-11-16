"""
DueClock Automated Daily Cadence Dispatch Engine & Background Worker
Evaluates all open invoices across tenants, calculates statutory timelines,
filters out disputed/promised bills, and dispatches scheduled multi-lingual WhatsApp reminders.
"""

from typing import List, Dict, Any, Optional
from datetime import date, datetime, timezone
from pydantic import BaseModel, Field

from dueclock.models import Invoice, SupplierProfile, StatutoryEvaluation, CadenceStage, SupportedLanguage
from dueclock.engine import StatutoryEngine
from dueclock.cadence import WhatsAppCadenceManager
from dueclock.bsp_adapter import WhatsAppBSPAdapter, WhatsAppDispatchRecord


class CadenceBatchSummary(BaseModel):
    batch_id: str
    execution_date: date
    total_invoices_evaluated: int
    reminders_dispatched: int
    cadence_stages_breakdown: Dict[str, int]
    skipped_paused_count: int      # Skipped due to active promise date or dispute
    total_cost_in_inr: float
    dispatched_records: List[Dict[str, Any]] = []


class CadenceWorkerEngine:
    """
    Automated execution worker that runs each morning to evaluate statutory debt clocks
    and dispatch transactional utility WhatsApp notices.
    """

    def __init__(self, bsp_adapter: Optional[WhatsAppBSPAdapter] = None):
        self.engine = StatutoryEngine()
        self.cadence_mgr = WhatsAppCadenceManager()
        self.bsp = bsp_adapter or WhatsAppBSPAdapter(use_mock=True)
        self.batch_logs: List[CadenceBatchSummary] = []

    def execute_daily_cadence_run(
        self,
        invoices: List[Invoice],
        supplier: SupplierProfile,
        as_of_date: Optional[date] = None,
        default_language: SupportedLanguage = SupportedLanguage.ENGLISH
    ) -> CadenceBatchSummary:
        if as_of_date is None:
            as_of_date = date.today()

        batch_id = f"batch_{as_of_date.strftime('%Y%m%d')}_{int(datetime.now().timestamp())}"
        dispatched_count = 0
        skipped_count = 0
        stages_count = {
            CadenceStage.STAGE_MINUS_5.value: 0,
            CadenceStage.STAGE_0.value: 0,
            CadenceStage.STAGE_PLUS_5.value: 0,
            CadenceStage.STAGE_PLUS_15.value: 0,
            CadenceStage.STAGE_PLUS_30.value: 0
        }
        dispatch_records_list: List[Dict[str, Any]] = []

        for inv in invoices:
            # 1. Skip if already fully settled
            if inv.outstanding_amount <= 0:
                continue

            # 2. Skip if buyer flagged active commercial/quality dispute
            if inv.status.value == "DISPUTED":
                skipped_count += 1
                continue

            # 3. Skip if buyer promised a payment date and scheduled date has not arrived yet
            if inv.promised_payment_date and as_of_date < inv.promised_payment_date:
                skipped_count += 1
                continue

            # 4. Evaluate statutory timeline
            eval_res = self.engine.evaluate_invoice(inv, supplier, as_of_date=as_of_date)

            # 5. Determine cadence stage
            stage = self.cadence_mgr.determine_cadence_stage(eval_res)
            if not stage:
                continue

            # 6. Generate message and dispatch through BSP
            msg_text = self.cadence_mgr.generate_message(
                stage=stage,
                invoice=inv,
                supplier=supplier,
                evaluation=eval_res,
                language=default_language
            )

            record = self.bsp.dispatch_utility_reminder(
                recipient_mobile=inv.buyer.mobile,
                template_name=f"dueclock_{stage.value.lower()}",
                language_code=default_language.value,
                parameters=[
                    inv.buyer.buyer_name,
                    inv.invoice_number,
                    f"{inv.outstanding_amount:,.2f}",
                    eval_res.statutory_due_date.strftime("%d-%b-%Y"),
                    f"upi://pay?pa={supplier.upi_vpa}"
                ]
            )

            dispatched_count += 1
            stages_count[stage.value] = stages_count.get(stage.value, 0) + 1
            dispatch_records_list.append({
                "invoice_number": inv.invoice_number,
                "buyer_name": inv.buyer.buyer_name,
                "stage": stage.value,
                "mobile": inv.buyer.mobile,
                "delivery_status": record.delivery_status.value,
                "cost": record.cost_in_inr
            })

        total_cost = round(dispatched_count * 0.145, 2)

        summary = CadenceBatchSummary(
            batch_id=batch_id,
            execution_date=as_of_date,
            total_invoices_evaluated=len(invoices),
            reminders_dispatched=dispatched_count,
            cadence_stages_breakdown=stages_count,
            skipped_paused_count=skipped_count,
            total_cost_in_inr=total_cost,
            dispatched_records=dispatch_records_list
        )
        self.batch_logs.append(summary)
        return summary
