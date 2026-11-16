import json
import logging
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from dueclock.connector.cli import main as cli_main
from dueclock.connector.config import ConnectorConfig
from dueclock.connector.local_store import LocalStore
from dueclock.connector.service import ConnectorService, RedactKey
from dueclock.connector.tally_client import TallyClient
from dueclock.connector.uploader import Uploader
from dueclock.core.api import create_core_app
from fake_tally import FakeTally, bill, flat_xml, nested_xml

NOW = datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)


class Env:
    def __init__(self, tmp_path, tally, key=None, state=None):
        self.app = create_core_app(str(tmp_path / "cloud"), today_fn=lambda: date(2026, 11, 20))
        self.tid, self.key = self.app.state.control.create_tenant("Precision Metal Works")
        self.http = TestClient(self.app)
        self.h = {"X-API-Key": self.key}
        self.http.put("/v1/supplier", json={"business_name": "P", "category": "MICRO", "activity": "MANUFACTURING", "udyam_registration_date": "2024-01-10"}, headers=self.h)
        self.cfg = ConnectorConfig(cloud_url="http://testserver", api_key=key or self.key, tally_port=tally.port,
                                   state_path=state or str(tmp_path / "state.db"), mass_settlement_min=3).validate()
        self.store = LocalStore(self.cfg.state_path)
        self.svc = ConnectorService(self.cfg, self.store, TallyClient("127.0.0.1", tally.port, 5), Uploader(self.cfg, self.store, self.http))

    def invoices(self, paid=True):
        return {i["invoice_number"]: i for i in self.http.get(f"/v1/invoices?include_paid={str(paid).lower()}", headers=self.h).json()["invoices"]}


def test_first_sync_end_to_end_and_unchanged_second_run(tmp_path):
    with FakeTally(nested_xml([bill(1, credit=45), bill(2, party="Kaveri Hydraulics", opening=50000.0)])) as t:
        e = Env(tmp_path, t)
        r = e.svc.run_once(now=NOW)
        assert (r.status, r.fetched, r.queued, r.sent_records, r.tally_product) == ("ok", 2, 2, 2, "TallyPrime")
        inv = e.invoices()
        assert set(inv) == {"INV/1", "INV/2"} and inv["INV/1"]["clock"]["due_date"] == "2026-10-16"
        again = e.svc.run_once(now=NOW)
        assert (again.changed, again.queued, again.sent_records) == (0, 0, 0)
        log = e.http.get("/v1/syncs", headers=e.h).json()["syncs"]
        assert len(log) == 1 and log[0]["connector_version"] == "0.1.0" and log[0]["tally_version"] == "TallyPrime"


def test_payment_and_settlement_are_reported(tmp_path):
    with FakeTally(nested_xml([bill(1), bill(2)])) as t:
        e = Env(tmp_path, t); e.svc.run_once(now=NOW)
        t.body = nested_xml([bill(1, closing=40000.0), bill(2)])                      # part payment on INV/1
        r = e.svc.run_once(now=NOW)
        assert r.changed == 1 and e.invoices()["INV/1"]["outstanding"] == 40000.0 and e.invoices()["INV/1"]["status"] == "PARTIALLY_PAID"
        t.body = nested_xml([bill(2)])                                                 # INV/1 settled: vanishes from Tally
        r = e.svc.run_once(now=NOW)
        assert r.settled_reported == 1 and e.invoices()["INV/1"]["status"] == "PAID" and e.invoices()["INV/2"]["status"] == "PENDING"


def test_mass_vanish_is_held_until_confirmed(tmp_path):
    with FakeTally(nested_xml([bill(i) for i in range(1, 11)])) as t:
        e = Env(tmp_path, t); e.svc.run_once(now=NOW)
        t.body = nested_xml([bill(1), bill(2)])                                        # wrong company open? 8 of 10 vanish
        r = e.svc.run_once(now=NOW)
        assert r.status == "held" and r.held_vanished == 8 and r.settled_reported == 0
        assert all(i["status"] == "PENDING" for i in e.invoices().values())
        r2 = e.svc.run_once(now=NOW, confirm_vanished=True)
        assert r2.settled_reported == 8 and sum(1 for i in e.invoices().values() if i["status"] == "PAID") == 8


def test_flat_report_keeps_original_amount_and_detects_partial_payment(tmp_path):
    with FakeTally(flat_xml([dict(bill(1, closing=100000.0), due="20261016")])) as t:
        e = Env(tmp_path, t); e.svc.run_once(now=NOW)
        t.body = flat_xml([dict(bill(1, closing=40000.0), due="20261016")])
        e.svc.run_once(now=NOW)
        i = e.invoices()["INV/1"]
        assert i["amount"] == 100000.0 and i["outstanding"] == 40000.0 and i["status"] == "PARTIALLY_PAID"


def test_tally_down_still_flushes_the_queue_and_restart_keeps_it(tmp_path):
    with FakeTally(nested_xml([bill(1)])) as t:
        state = str(tmp_path / "state.db")
        e = Env(tmp_path, t, state=state)
        down = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
        e.svc.uploader = Uploader(e.cfg, e.store, down)                                # cloud unreachable
        r = e.svc.run_once(now=NOW)
        assert r.queued == 1 and r.retried == 1 and e.invoices() == {} and r.queue_pending == 1
        e.store.close()
        # restart: new process, same state file, Tally now stopped, cloud back
        port = t.port
    store2 = LocalStore(state)
    svc2 = ConnectorService(e.cfg, store2, TallyClient("127.0.0.1", port, 1), Uploader(e.cfg, store2, e.http))
    from datetime import timedelta
    r2 = svc2.run_once(now=NOW + timedelta(days=1))
    assert r2.status == "tally_unreachable" and r2.sent_records == 1 and set(e.invoices()) == {"INV/1"}


def test_rejected_key_stops_uploads_and_keeps_data(tmp_path):
    with FakeTally(nested_xml([bill(1)])) as t:
        e = Env(tmp_path, t, key="dc_" + "z" * 40)
        r = e.svc.run_once(now=NOW)
        assert r.status == "auth_rejected" and r.queue_pending == 1 and e.invoices() == {}


def test_garbage_response_does_not_touch_the_snapshot(tmp_path):
    with FakeTally(nested_xml([bill(1)])) as t:
        e = Env(tmp_path, t); e.svc.run_once(now=NOW)
        t.body = "<broken"
        r = e.svc.run_once(now=NOW)
        assert r.status == "parse_failed" and r.settled_reported == 0 and e.invoices()["INV/1"]["status"] == "PENDING"
        t.status = 500
        assert e.svc.run_once(now=NOW).status == "tally_error"


def test_dry_run_changes_nothing(tmp_path):
    with FakeTally(nested_xml([bill(1)])) as t:
        e = Env(tmp_path, t)
        r = e.svc.run_once(dry_run=True, now=NOW)
        assert r.fetched == 1 and r.changed == 1 and r.queued == 0 and e.store.queue_stats()["pending"] == 0 and e.invoices() == {}


def test_diagnose_and_ping(tmp_path):
    with FakeTally(nested_xml([bill(1)])) as t:
        e = Env(tmp_path, t)
        t.body = "<ENVELOPE><COMPANY><NAME>Precision Metal Works</NAME></COMPANY></ENVELOPE>"
        d = e.svc.diagnose()
        assert d["tally"]["running"] and d["tally"]["companies"] == ["Precision Metal Works"]
        assert d["cloud"]["ok"] and d["cloud"]["tenant"] == "Precision Metal Works" and d["cloud"]["min_connector_version"] == "0.1.0"


def test_scheduler_next_run(tmp_path):
    with FakeTally() as t:
        e = Env(tmp_path, t)
        assert e.svc.next_run_after(datetime(2026, 11, 20, 1, 0)) == datetime(2026, 11, 20, 2, 0)
        assert e.svc.next_run_after(datetime(2026, 11, 20, 2, 0)) == datetime(2026, 11, 21, 2, 0)


def test_api_key_is_redacted_from_logs():
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "key=%s ok", ("dc_secret",), None)
    RedactKey("dc_secret").filter(rec)
    assert "dc_secret" not in rec.getMessage() and "[key]" in rec.getMessage()


def test_cli_probe_run_once_and_import(tmp_path, capsys, monkeypatch):
    with FakeTally(nested_xml([bill(1)])) as t:
        e = Env(tmp_path, t)
        p = tmp_path / "connector.json"
        p.write_text(json.dumps({"cloud_url": "http://testserver", "tally_port": t.port, "state_path": str(tmp_path / "cli.db"), "mass_settlement_min": 3}))
        monkeypatch.setenv("DUECLOCK_API_KEY", e.key)
        assert cli_main(["probe", "--config", str(p)]) == 0
        assert "TallyPrime" in capsys.readouterr().out
        assert cli_main(["run-once", "--dry-run", "--config", str(p)]) == 0
        csvf = tmp_path / "bills.csv"
        csvf.write_text("Bill Ref,Date,Party Name,Amount,Pending\nX1,01/10/2026,Acme,\"10,000\",\"4,000\"\nX2,bad,Acme,1,1\n")
        assert cli_main(["import-export", "--dry-run", "--config", str(p), str(csvf)]) == 0
        assert "parsed 1 rows, 1 errors" in capsys.readouterr().out
        assert cli_main(["probe", "--config", str(tmp_path / "missing.json")]) == 2
