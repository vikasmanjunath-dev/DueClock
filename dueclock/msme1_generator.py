"""
DueClock Form MSME-1 Half-Yearly Return Filing Generator
Under Section 405 of the Companies Act, 2013 and MCA Notification S.O. 368(E).
Generates compliant filing schedules for buyers to submit on the MCA portal
for delayed MSME payments (due April 30 and October 31, avoiding ₹3 lakh penalties).
"""

from typing import List, Dict, Any, Optional
from datetime import date
from pydantic import BaseModel
import csv
import io
import json


class MSME1Entry(BaseModel):
    supplier_name: str
    supplier_pan: str
    supplier_udyam_reg: str
    invoice_number: str
    invoice_date: date
    statutory_due_date: date
    amount_due: float
    delay_in_days: int
    reason_for_delay: str = "Invoice under dispute or administrative processing"


class MSME1Schedule(BaseModel):
    reporting_company_name: str
    reporting_company_cin: str
    reporting_company_gstin: str
    financial_year: str
    half_year_period: str  # "October to March" (Due April 30) or "April to September" (Due October 31)
    total_delayed_msme_amount: float
    total_delayed_vendors_count: int
    entries: List[MSME1Entry]


class MSME1Generator:
    """
    Constructs statutory MCA Form MSME-1 filings.
    Protects corporate buyers and directors from Section 405 penalties (up to ₹3 Lakh fine & imprisonment).
    """

    @staticmethod
    def determine_half_year_period(as_of_date: Optional[date] = None) -> tuple[str, str, date]:
        """Returns (half_year_period, fiscal_year, statutory_filing_deadline)."""
        if as_of_date is None:
            as_of_date = date.today()

        year = as_of_date.year
        # First half: April to Sept -> Due Oct 31
        # Second half: Oct to March -> Due April 30
        if 4 <= as_of_date.month <= 9:
            period = "April to September"
            fy = f"FY {year}-{str(year+1)[-2:]}"
            deadline = date(year, 10, 31)
        elif as_of_date.month >= 10:
            period = "October to March"
            fy = f"FY {year}-{str(year+1)[-2:]}"
            deadline = date(year + 1, 4, 30)
        else: # Jan - March
            period = "October to March"
            fy = f"FY {year-1}-{str(year)[-2:]}"
            deadline = date(year, 4, 30)

        return (period, fy, deadline)

    def generate_schedule(
        self,
        company_name: str,
        company_cin: str,
        company_gstin: str,
        delayed_entries: List[MSME1Entry],
        as_of_date: Optional[date] = None
    ) -> MSME1Schedule:
        period, fy, _ = self.determine_half_year_period(as_of_date)
        total_amt = sum(e.amount_due for e in delayed_entries)
        unique_vendors = len(set(e.supplier_pan for e in delayed_entries))

        return MSME1Schedule(
            reporting_company_name=company_name,
            reporting_company_cin=company_cin,
            reporting_company_gstin=company_gstin,
            financial_year=fy,
            half_year_period=period,
            total_delayed_msme_amount=round(total_amt, 2),
            total_delayed_vendors_count=unique_vendors,
            entries=delayed_entries
        )

    def export_csv(self, schedule: MSME1Schedule) -> str:
        """Exports MCA upload-ready CSV format."""
        output = io.StringIO()
        writer = csv.writer(output)

        # Header Metadata
        writer.writerow(["MCA FORM MSME-1 FILING SCHEDULE"])
        writer.writerow(["Company Name", schedule.reporting_company_name])
        writer.writerow(["CIN", schedule.reporting_company_cin])
        writer.writerow(["Period", schedule.half_year_period])
        writer.writerow(["Financial Year", schedule.financial_year])
        writer.writerow(["Total Delayed MSME Amount", f"{schedule.total_delayed_msme_amount:.2f}"])
        writer.writerow([])

        # Table Headers
        writer.writerow([
            "Sr No",
            "Supplier Name",
            "Supplier PAN",
            "Udyam Number",
            "Invoice No",
            "Invoice Date",
            "Statutory Due Date",
            "Amount Due (INR)",
            "Days Delayed",
            "Reason for Delay"
        ])

        for idx, e in enumerate(schedule.entries, start=1):
            writer.writerow([
                idx,
                e.supplier_name,
                e.supplier_pan,
                e.supplier_udyam_reg,
                e.invoice_number,
                e.invoice_date.strftime("%d/%m/%Y"),
                e.statutory_due_date.strftime("%d/%m/%Y"),
                f"{e.amount_due:.2f}",
                e.delay_in_days,
                e.reason_for_delay
            ])

        return output.getvalue()
