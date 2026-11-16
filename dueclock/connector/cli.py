"""Connector command line.

    python -m dueclock.connector probe          --config connector.json
    python -m dueclock.connector diagnose       --config connector.json
    python -m dueclock.connector run-once       --config connector.json [--dry-run] [--confirm-vanished]
    python -m dueclock.connector import-export  --config connector.json bills.csv|bills.json
    python -m dueclock.connector status         --config connector.json
    python -m dueclock.connector serve          --config connector.json
"""
import argparse
import csv
import getpass
import json
import os
import sys

from dueclock.core.tally_parse import parse_export_rows

from . import CONNECTOR_VERSION
from .config import ConfigError, ConnectorConfig
from .paths import default_paths
from .setup_cmd import read_params_file, run_setup
from .task_scheduler import action_for_run, install_task, remove_task, task_status, task_xml
from .service import ConnectorService, setup_logging
from .uploader import build_payload


def _load_rows(path: str):
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("records", [])
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _setup(a) -> int:
    if a.from_file:
        try:
            params = read_params_file(a.from_file)
        except (OSError, ValueError) as e:
            print(f"error: cannot read the setup answers ({type(e).__name__})", file=sys.stderr)
            return 2
    else:
        params = {"cloud_url": input("Cloud address (https://...): ").strip(), "api_key": getpass.getpass("Access key (hidden): ").strip(),
                  "tally_port": input("Tally port [9000]: ").strip() or 9000}
    res = run_setup(params, config_path=None if a.config in ("connector.json",) else a.config)
    for name, c in res["checks"].items():
        print(f"{name}: {c}")
    for e in res["errors"]:
        print("error:", e, file=sys.stderr)
    print(f"config written to {res['config_path']}" if res["ok"] else "setup failed; nothing was written")
    return 0 if res["ok"] else 2


def _tally_check(a) -> int:
    from .tally_check import render_text, run_check, save_profile
    from .tally_client import TallyClient
    host, port, bills_tpl = "127.0.0.1", 9000, None
    try:
        cfg = ConnectorConfig.load(a.config)
        host, port = cfg.tally_host, cfg.tally_port
        if cfg.tally_bills_template_path:
            with open(cfg.tally_bills_template_path, "r", encoding="utf-8-sig") as f:
                bills_tpl = f.read()
    except (OSError, ConfigError, ValueError):
        pass                                                           # the check also works before setup, against the default port
    out = a.out_dir or os.path.join(default_paths()["dir"], "tally-check", __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S"))
    rep = run_check(TallyClient(host, port, 30.0), company=a.company, out_dir=out, redact=not a.no_redact, bills_template=bills_tpl)
    print(render_text(rep))
    print("report folder:", out)
    if a.save_profile and rep["profile"]:
        try:
            save_profile(a.config, rep["profile"])
            print("saved working request variants to", a.config, rep["profile"])
        except OSError as e:
            print("could not save the profile:", e, file=sys.stderr)
    return {"PASS": 0, "PARTIAL": 0, "FAIL": 1}[rep["verdict"]]


def _task(a) -> int:
    cmd, args = action_for_run(os.path.abspath(a.config) if a.config != "connector.json" else default_paths()["config"])
    if a.command == "install-task":
        if a.xml_only:
            print(task_xml(cmd, args))
            return 0
        try:
            print(install_task(cmd, args))
        except OSError as e:
            print(f"could not install the scheduled task: {e}", file=sys.stderr)
            return 1
        return 0
    try:
        print(remove_task())
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dueclock-connector")
    p.add_argument("command", choices=["probe", "diagnose", "run-once", "import-export", "status", "serve", "setup", "install-task", "remove-task", "version", "doctor", "tally-check", "tally-fixture"])
    p.add_argument("file", nargs="?", help="import-export: the export file; tally-fixture: the capture to turn into a test fixture")
    p.add_argument("--name", default=None, help="tally-fixture: short label for the Tally version, e.g. prime-5-0")
    p.add_argument("--dest", default=os.path.join("tests", "fixtures", "tally"), help="tally-fixture: folder to save into")
    p.add_argument("--config", default=None)
    p.add_argument("--from-file", default=None, help="setup answers as JSON (the installer uses this; the file is wiped after reading)")
    p.add_argument("--company", default=None, help="tally-check: company to read")
    p.add_argument("--out-dir", default=None, help="tally-check: folder for the report and captures")
    p.add_argument("--no-redact", action="store_true", help="tally-check: keep raw replies (they contain real names; do not share)")
    p.add_argument("--save-profile", action="store_true", help="tally-check: record the request variants that worked in the config")
    p.add_argument("--xml-only", action="store_true", help="install-task: print the task definition instead of registering it")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm-vanished", action="store_true")
    a = p.parse_intermixed_args(argv)
    a.config = a.config or (default_paths()["config"] if os.path.exists(default_paths()["config"]) else "connector.json")
    if a.command == "version":
        print(f"dueclock-connector {CONNECTOR_VERSION}")
        return 0
    if a.command == "setup":
        return _setup(a)
    if a.command == "tally-check":
        return _tally_check(a)
    if a.command == "tally-fixture":
        if not a.file or not a.name:
            print("usage: tally-fixture <capture.xml> --name <label>", file=sys.stderr)
            return 2
        from .tally_check import import_fixture
        try:
            res = import_fixture(a.file, a.dest, a.name)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(json.dumps(res, indent=2, default=str))
        return 0 if res["ok"] else 1
    if a.command in ("install-task", "remove-task"):
        return _task(a)
    try:
        cfg = ConnectorConfig.load(a.config)
    except (ConfigError, OSError, ValueError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    setup_logging(cfg)
    svc = ConnectorService(cfg)
    if a.command == "probe":
        pr = svc.tally.probe()
        print(json.dumps({"running": pr.running, "product": pr.product, "message": pr.message}))
        return 0 if pr.running else 1
    if a.command in ("diagnose", "doctor"):
        d = svc.diagnose()
        d["task"] = task_status()
        d["config_path"] = a.config
        print(json.dumps(d, indent=2, default=str))
        return 0
    if a.command == "status":
        print(json.dumps({"queue": svc.store.queue_stats(), "last_runs": svc.store.last_runs(5)}, indent=2, default=str))
        return 0
    if a.command == "import-export":
        if not a.file:
            p.error("export file required")
        parsed = parse_export_rows(_load_rows(a.file))
        print(f"parsed {len(parsed.records)} rows, {len(parsed.errors)} errors")
        for e in parsed.errors[:10]:
            print("  -", e)
        if parsed.records and not a.dry_run:
            for i in range(0, len(parsed.records), cfg.batch_size):
                svc.store.enqueue(build_payload(parsed.records[i:i + cfg.batch_size], "export-import"))
            print(f"flushed: {svc.uploader.flush().records_sent} records sent")
        return 0 if parsed.records else 1
    if a.command == "serve":
        svc.serve_forever()
        return 0
    res = svc.run_once(dry_run=a.dry_run, confirm_vanished=a.confirm_vanished)
    print(json.dumps(res.__dict__, indent=2, default=str))
    return 0 if res.status in ("ok", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
