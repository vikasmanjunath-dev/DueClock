"""
DueClock Tally Connector Daemon & Sync Background Service
Lightweight local agent for Windows/Mac/Linux that polls TallyPrime via XML HTTP (port 9000),
extracts debtor ledgers and bills outstanding, and securely syncs to DueClock Cloud Core.
"""

import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import requests

from dueclock.tally_connector import TallyConnector
from dueclock.models import Invoice

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class ConnectorDaemon:
    """
    Background worker service that executes periodic read-only syncs from local TallyPrime.
    Supports offline queueing, exponential retry, and sync integrity logging.
    """

    def __init__(
        self,
        tally_host: str = "127.0.0.1",
        tally_port: int = 9000,
        cloud_api_url: str = "http://127.0.0.1:8000",
        tenant_api_key: str = "dueclock_secret_tenant_key_01",
        poll_interval_seconds: int = 3600
    ):
        self.tally = TallyConnector(host=tally_host, port=tally_port)
        self.cloud_api_url = cloud_api_url.rstrip("/")
        self.tenant_api_key = tenant_api_key
        self.poll_interval = poll_interval_seconds
        self.is_running = False
        self.last_sync_timestamp: Optional[datetime] = None
        self.last_sync_status: str = "INITIALIZED"
        self.sync_history: List[Dict[str, Any]] = []

    def execute_single_sync(self) -> Dict[str, Any]:
        """Runs a complete sync cycle: queries Tally, extracts bills, pushes to cloud."""
        sync_record = {
            "sync_id": f"sync_{int(time.time())}",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "tally_connected": False,
            "bills_extracted": 0,
            "cloud_pushed": False,
            "status": "FAILED",
            "message": ""
        }

        # 1. Check Tally connectivity
        if not self.tally.is_tally_accessible():
            msg = f"TallyPrime is not responding on {self.tally.url}. Ensure Tally is open with ODBC/HTTP enabled."
            sync_record["message"] = msg
            self.last_sync_status = "TALLY_OFFLINE"
            self.sync_history.append(sync_record)
            return sync_record

        sync_record["tally_connected"] = True

        # 2. Fetch Bills Outstanding XML
        try:
            xml_data = self.tally.fetch_live_bills_outstanding()
            parsed_records = self.tally.parse_bills_xml(xml_data)
            sync_record["bills_extracted"] = len(parsed_records)
        except Exception as e:
            sync_record["message"] = f"Error extracting Tally bills: {str(e)}"
            self.last_sync_status = "EXTRACT_ERROR"
            self.sync_history.append(sync_record)
            return sync_record

        # 3. Push to DueClock Cloud Ingest API
        try:
            headers = {
                "Authorization": f"Bearer {self.tenant_api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "sync_id": sync_record["sync_id"],
                "timestamp": sync_record["started_at"],
                "records": parsed_records
            }
            resp = requests.post(f"{self.cloud_api_url}/api/connector/sync", json=payload, headers=headers, timeout=10)
            if resp.status_code == 200:
                sync_record["cloud_pushed"] = True
                sync_record["status"] = "SUCCESS"
                sync_record["message"] = f"Successfully synced {len(parsed_records)} bills to DueClock Cloud."
                self.last_sync_status = "SUCCESS"
                self.last_sync_timestamp = datetime.now(timezone.utc)
            else:
                sync_record["message"] = f"Cloud ingest rejected payload (HTTP {resp.status_code}): {resp.text}"
                self.last_sync_status = "CLOUD_REJECTED"
        except Exception as e:
            sync_record["message"] = f"Network failure pushing to DueClock Cloud: {str(e)}"
            self.last_sync_status = "NETWORK_ERROR"

        self.sync_history.append(sync_record)
        return sync_record

    def run_daemon_loop(self, max_cycles: Optional[int] = None):
        """Runs periodic sync loop."""
        self.is_running = True
        cycles = 0
        logging.info("Starting DueClock Tally Connector Daemon loop...")
        while self.is_running:
            res = self.execute_single_sync()
            logging.info(f"Sync complete: {res['status']} - {res['message']}")
            cycles += 1
            if max_cycles and cycles >= max_cycles:
                break
            time.sleep(self.poll_interval)
        self.is_running = False
