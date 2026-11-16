import json
import os

import pytest
from fastapi.testclient import TestClient

from dueclock.core import cli
from dueclock.core.api import create_core_app
from dueclock.core.preflight import check_env, enforce_in_production, failures

GOOD = {
    "DUECLOCK_ENV": "production", "DUECLOCK_PUBLIC_URL": "https://dueclock.example.com", "DUECLOCK_TOKEN_SECRET": "a3f9c1d27b5e48f0916c2d7e5b8a40f3c6d19e2b7a54f801",
    "DUECLOCK_CONTROL_DB_URL": "postgresql://postgres.abc@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres?schema=dueclock_control&sslmode=require",
    "DUECLOCK_TENANT_DB_URL": "postgresql://postgres.abc@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres?schema=dueclock&sslmode=require",
    "PGPASSWORD": "pw", "DUECLOCK_TRUST_PROXY": "1", "DUECLOCK_WA_MODE": "cloud", "DUECLOCK_WA_PHONE_NUMBER_ID": "1", "DUECLOCK_WA_TOKEN": "t",
    "DUECLOCK_WA_APP_SECRET": "s", "DUECLOCK_WA_VERIFY_TOKEN": "v", "DUECLOCK_SCHEDULER": "1",
}
codes = lambda env: {f["code"] for f in check_env(env)}
fails = lambda env: {f["code"] for f in failures(check_env(env))}


def test_a_complete_production_environment_passes():
    res = check_env(GOOD)
    assert failures(res) == [] and not [f for f in res if f["level"] == "warn"] and res[-1]["code"] == "READY"


@pytest.mark.parametrize("key,bad,code", [
    ("DUECLOCK_PUBLIC_URL", "http://dueclock.example.com", "PUBLIC_URL_NOT_HTTPS"),
    ("DUECLOCK_PUBLIC_URL", "https://localhost", "PUBLIC_URL_NOT_HTTPS"),
    ("DUECLOCK_PUBLIC_URL", None, "PUBLIC_URL_MISSING"),
    ("DUECLOCK_TOKEN_SECRET", None, "TOKEN_SECRET_MISSING"),
    ("DUECLOCK_TOKEN_SECRET", "short", "TOKEN_SECRET_WEAK"),
    ("DUECLOCK_TOKEN_SECRET", "changeme", "TOKEN_SECRET_WEAK"),
    ("DUECLOCK_TOKEN_SECRET", "a" * 40, "TOKEN_SECRET_WEAK"),
    ("DUECLOCK_CONTROL_DB_URL", None, "CONTROL_DB_MISSING"),
    ("DUECLOCK_CONTROL_DB_URL", "sqlite:///data/control.db", "CONTROL_DB_NOT_POSTGRES"),
    ("DUECLOCK_CONTROL_DB_URL", "postgresql://u@h:5432/postgres?sslmode=require", "CONTROL_DB_SCHEMA"),
    ("DUECLOCK_TENANT_DB_URL", "postgresql://u@h:5432/postgres?sslmode=require", "TENANT_DB_SCHEMA"),
    ("PGPASSWORD", None, "DB_PASSWORD_MISSING"),
    ("DUECLOCK_WA_TOKEN", None, "WA_INCOMPLETE"),
])
def test_each_unsafe_setting_blocks_production(key, bad, code):
    env = dict(GOOD)
    if bad is None:
        env.pop(key)
    else:
        env[key] = bad
    assert code in fails(env), codes(env)


def test_same_schema_for_control_and_customers_is_blocked():
    env = dict(GOOD, DUECLOCK_TENANT_DB_URL=GOOD["DUECLOCK_CONTROL_DB_URL"])
    assert "SAME_SCHEMA" in fails(env)


def test_warnings_do_not_block_but_are_reported():
    env = dict(GOOD)
    for k in ("DUECLOCK_TRUST_PROXY", "DUECLOCK_WA_MODE", "DUECLOCK_TENANT_DB_URL", "DUECLOCK_SCHEDULER"):
        env.pop(k)
    env["DUECLOCK_CONTROL_DB_URL"] = "postgresql://u:secretpw@h:5432/postgres?schema=c"
    res = check_env(env)
    assert failures(res) == []
    w = {f["code"] for f in res if f["level"] == "warn"}
    assert {"PROXY_NOT_TRUSTED", "NO_REMINDER_SCHEDULER", "WA_NOT_LIVE", "TENANT_DB_MISSING", "CONTROL_DB_PASSWORD_IN_URL", "CONTROL_DB_SSL"} <= w
    assert "secretpw" not in json.dumps(res)                                          # never echoes secrets


def test_production_refuses_to_start_on_a_bad_environment_but_dev_does_not():
    with pytest.raises(RuntimeError, match="TOKEN_SECRET_MISSING"):
        enforce_in_production({k: v for k, v in GOOD.items() if k != "DUECLOCK_TOKEN_SECRET"})
    enforce_in_production({})                                                         # no DUECLOCK_ENV: development, never blocks
    enforce_in_production({"DUECLOCK_ENV": "development"})
    enforce_in_production(GOOD)


def test_preflight_cli_exit_codes_and_output(monkeypatch, capsys):
    for k in list(os.environ):
        if k.startswith(("DUECLOCK_", "PG")):
            monkeypatch.delenv(k)
    monkeypatch.setattr("sys.argv", ["dueclock-core", "preflight"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    out = capsys.readouterr().out
    assert e.value.code == 1 and "[FAIL]" in out and "TOKEN_SECRET_MISSING" in out
    for k, v in GOOD.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0 and "READY" in capsys.readouterr().out


def test_probe_reports_connection_failures_without_secrets(monkeypatch):
    pytest.importorskip("psycopg2")
    env = dict(GOOD, DUECLOCK_CONTROL_DB_URL="postgresql://u:pw123secret@127.0.0.1:1/postgres?schema=c&sslmode=disable")
    res = check_env(env, probe_db=True)
    assert any(f["code"] == "CONTROL_DB_CONNECT" for f in failures(res)) and "pw123secret" not in json.dumps(res)


def test_ready_endpoint_reflects_the_control_database(tmp_path):
    app = create_core_app(str(tmp_path))
    c = TestClient(app)
    assert c.get("/v1/health").json()["status"] == "ok" and c.get("/v1/ready").json() == {"status": "ready"}
    app.state.control.db.close()
    assert c.get("/v1/ready").status_code == 503


def test_serve_passes_host_port_and_proxy_settings(monkeypatch):
    import sys
    import types
    calls = {}
    fake = types.SimpleNamespace(run=lambda *a, **k: calls.update(args=a, kw=k))
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("DUECLOCK_TRUST_PROXY", "1")
    monkeypatch.setattr("sys.argv", ["dueclock-core", "serve", "--host", "0.0.0.0"])
    cli.main()
    assert calls["kw"]["host"] == "0.0.0.0" and calls["kw"]["port"] == 10000 and calls["kw"]["forwarded_allow_ips"] == "*" and calls["kw"]["proxy_headers"]
    monkeypatch.delenv("DUECLOCK_TRUST_PROXY")
    monkeypatch.delenv("PORT")
    cli.main()
    assert calls["kw"]["forwarded_allow_ips"] is None and calls["kw"]["port"] == 8100 and calls["kw"]["host"] == "0.0.0.0"


# ---- scheduler
class _Ctl:
    def __init__(self, tenants):
        self.tenants = tenants
    def list_tenants(self):
        return self.tenants
    def context_by_id(self, i):
        return i
    def store_for(self, ctx):
        return ctx


class _Eng:
    def __init__(self, boom=()):
        self.ran, self.boom = [], set(boom)
    def run(self, store):
        self.ran.append(store)
        if store in self.boom:
            raise RuntimeError("provider down")
        from types import SimpleNamespace
        return SimpleNamespace(sent=2, failed=1)


def test_scheduler_runs_active_tenants_and_isolates_failures():
    from dueclock.core.scheduler import ReminderScheduler
    eng = _Eng(boom={"b"})
    s = ReminderScheduler(_Ctl([{"id": "a", "active": 1}, {"id": "b", "active": 1}, {"id": "c", "active": 0}, {"id": "d", "active": 1}]), eng)
    assert s.run_once() == {"tenants": 3, "sent": 4, "failed": 2, "errors": 1}
    assert eng.ran == ["a", "b", "d"] and s.last["errors"] == 1                      # b failed, d still ran, inactive c skipped


def test_scheduler_thread_starts_and_stops(monkeypatch):
    import time
    from dueclock.core.scheduler import ReminderScheduler
    eng = _Eng()
    s = ReminderScheduler(_Ctl([{"id": "a", "active": 1}]), eng, interval_s=3600, first_delay_s=0)
    s.start()
    for _ in range(100):
        if eng.ran:
            break
        time.sleep(0.02)
    s.stop()
    assert eng.ran == ["a"] and not s._thread.is_alive()


def test_scheduler_is_wired_only_when_enabled(tmp_path):
    from dueclock.core.scheduler import attach
    app = create_core_app(str(tmp_path))
    assert attach(app, {}) is None and attach(app, {"DUECLOCK_SCHEDULER": "0"}) is None
    s = attach(app, {"DUECLOCK_SCHEDULER": "1", "DUECLOCK_SCHEDULER_MINUTES": "15"})
    assert s.interval_s == 900 and app.state.scheduler is s


def test_scheduler_sends_a_real_reminder_end_to_end(tmp_path):
    from datetime import datetime, timezone
    from dueclock.core.messaging import MockSender
    from dueclock.core.reminders import IST
    from dueclock.core.scheduler import ReminderScheduler
    now = datetime(2027, 1, 17, 5, 0, tzinfo=timezone.utc)
    sender = MockSender()
    app = create_core_app(str(tmp_path), today_fn=lambda: now.astimezone(IST).date(), now_fn=lambda: now, public_base_url="http://b.test", sender=sender)
    c = TestClient(app)
    tid, key = app.state.control.create_tenant("Alpha", "sqlite:///" + str(tmp_path / "t.db"))
    h = {"X-API-Key": key}
    c.put("/v1/supplier", json={"business_name": "P", "category": "MICRO", "activity": "MANUFACTURING", "udyam_registration_date": "2024-01-10", "upi_vpa": "pp@icici"}, headers=h)
    c.post("/v1/connector/sync", json={"connector_version": "0.1", "records": [dict(guid="g1", invoice_number="INV/1", invoice_date="2027-01-01", delivery_date="2027-01-01", buyer_name="Zenith", buyer_mobile="+91 98450 12345", amount=1000, pending_amount=1000)]}, headers=h)
    inv = c.get("/v1/invoices", headers=h).json()["invoices"][0]
    c.put(f"/v1/buyers/{inv['buyer_id']}/opt-in", json={"source": "pilot_note"}, headers=h)
    sch = ReminderScheduler(app.state.control, app.state.engine)
    assert sch.run_once()["sent"] == 1 and len(sender.sent) == 1
    assert sch.run_once()["sent"] == 0 and len(sender.sent) == 1                       # second runner (or second server) sends nothing more


# ---- deployment files
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
read = lambda p: open(os.path.join(ROOT, p), encoding="utf-8").read()


def test_dockerfile_rules():
    d = read("Dockerfile")
    assert "USER dueclock" in d and "DUECLOCK_ENV=production" in d and "--host\", \"0.0.0.0" in d and "HEALTHCHECK" in d
    assert "requirements-server.txt" in d and "requirements-connector" not in d
    for secret_word in ("PGPASSWORD", "TOKEN_SECRET", "dc_"):
        assert secret_word not in d.replace("DUECLOCK_TOKEN_SECRET", "")                      # no credentials baked into the image


def test_server_requirements_are_minimal_and_complete():
    r = read("requirements-server.txt").lower()
    for need in ("fastapi", "uvicorn", "pydantic", "httpx", "psycopg2"):
        assert need in r
    for never in ("pytest", "pyinstaller", "openpyxl"):
        assert never not in r


def test_dockerignore_keeps_data_and_secrets_out_of_the_image():
    ig = read(".dockerignore").split()
    for item in ("data", ".env*", "tests", "*.db"):
        assert item in ig
    gi = read(".gitignore").split()
    assert "data/" in gi and ".env" in gi and "*.db" in gi


def test_host_configs_hold_no_secrets_and_enable_the_safe_settings():
    for name in ("fly.toml", "render.yaml"):
        t = read(name)
        assert "DUECLOCK_ENV" in t and "production" in t and "DUECLOCK_TRUST_PROXY" in t and "DUECLOCK_SCHEDULER" in t and "/v1/ready" in t
        for secret in ("PGPASSWORD", "DUECLOCK_TOKEN_SECRET", "DUECLOCK_WA_TOKEN"):
            for line in t.splitlines():
                if secret in line and not line.lstrip().startswith("#"):
                    assert "sync: false" in line, f"{name}: {secret} must not carry a value"
    fly = read("fly.toml").replace("'", '"')                                           # `fly launch` rewrites the file with single quotes
    assert 'auto_stop_machines = "off"' in fly and 'primary_region = "nrt"' in fly and 'app = "dueclock"' in fly


def test_the_image_only_needs_modules_that_do_not_import_test_or_build_tools():
    import subprocess
    import sys
    code = "import sys; import dueclock.core.main as m; bad=[x for x in ('pytest','PyInstaller','openpyxl') if x in sys.modules]; print(','.join(bad))"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env={**os.environ, "DUECLOCK_DATA_DIR": os.path.join(ROOT, "data")})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_scheduler_logs_are_visible_and_announce_startup(tmp_path, capsys):
    import logging
    from dueclock.core.scheduler import attach
    app = create_core_app(str(tmp_path))
    s = attach(app, {"DUECLOCK_SCHEDULER": "1", "DUECLOCK_SCHEDULER_MINUTES": "30"})
    s.first_delay_s = 3600                                                              # keep the thread idle for this test
    with TestClient(app):                                                               # runs the startup handlers
        pass
    out = capsys.readouterr().out
    assert "reminder scheduler started" in out and "every 30 min" in out
    base = logging.getLogger("dueclock")
    assert sum(1 for h in base.handlers if getattr(h, "_dueclock", False)) == 1         # attaching again does not duplicate lines
    attach(create_core_app(str(tmp_path / "b")), {"DUECLOCK_SCHEDULER": "1"})
    assert sum(1 for h in base.handlers if getattr(h, "_dueclock", False)) == 1


# ---- serverless (Vercel) pieces
def test_transaction_pooler_port_is_now_accepted():
    env = dict(GOOD, DUECLOCK_CONTROL_DB_URL="postgresql://u@h:6543/postgres?schema=c&sslmode=require")
    assert failures(check_env(env)) == []


def test_cron_secret_counts_as_a_scheduler_and_serverless_scheduler_is_warned():
    env = dict(GOOD); env.pop("DUECLOCK_SCHEDULER"); env["CRON_SECRET"] = "x" * 32
    assert "NO_REMINDER_SCHEDULER" not in codes(env)
    assert "SCHEDULER_ON_SERVERLESS" in codes(dict(GOOD, VERCEL="1"))


def test_cron_endpoint_requires_the_secret_and_runs_reminders(tmp_path, monkeypatch):
    app = create_core_app(str(tmp_path))
    c = TestClient(app)
    monkeypatch.delenv("DUECLOCK_CRON_SECRET", raising=False); monkeypatch.delenv("CRON_SECRET", raising=False)
    assert c.get("/v1/cron/reminders").status_code == 401                                  # no secret configured: always refused
    monkeypatch.setenv("CRON_SECRET", "s3cret-s3cret-s3cret-s3cret-s3cret")
    assert c.get("/v1/cron/reminders").status_code == 401
    assert c.get("/v1/cron/reminders", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/v1/cron/reminders", headers={"Authorization": "Bearer s3cret-s3cret-s3cret-s3cret-s3cret"})
    assert r.status_code == 200 and r.json() == {"tenants": 0, "sent": 0, "failed": 0, "errors": 0}


def test_vercel_files():
    import json
    v = json.loads(read("vercel.json"))
    assert v["rewrites"][0]["destination"] == "/api/index" and v["crons"][0]["path"] == "/v1/cron/reminders"
    assert v["crons"][0]["schedule"].split()[1:] == ["4", "*", "*", "*"]                       # once a day (Hobby plan limit), 04:00 UTC = 09:30 IST
    assert read("requirements.txt").strip() == "-r requirements-server.txt"
    e = read("api/index.py")
    assert "enforce_in_production" in e and "/tmp/dueclock" in e and "create_core_app()" in e


def test_vercel_entry_imports_and_serves(monkeypatch, tmp_path):
    import importlib.util, sys
    monkeypatch.setenv("DUECLOCK_DATA_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("vercel_entry", os.path.join(ROOT, "api", "index.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert TestClient(mod.app).get("/v1/health").json()["status"] == "ok"
