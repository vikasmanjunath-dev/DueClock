import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import httpx
import pytest

from dueclock.connector import cli
from dueclock.connector.config import ConfigError, ConnectorConfig
from dueclock.connector.paths import default_dir, default_paths
from dueclock.connector.protect import PlainProtector, get_protector, unprotect_any
from dueclock.connector.setup_cmd import read_params_file, run_setup
from dueclock.connector.task_scheduler import action_for_run, task_xml

KEY = "dc_" + "k" * 30
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class FakeProbe:
    running, product = True, "TallyPrime"


class FakeTally:
    def __init__(self, *a, **k): pass
    def probe(self): return FakeProbe()


def http(status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json={"tenant": "t1"})))


def params(**kw):
    return {"cloud_url": "https://cloud.example.com", "api_key": KEY, "tally_port": 9000, **kw}


def test_paths_per_platform():
    assert default_dir({"LOCALAPPDATA": r"C:\Users\a\AppData\Local"}, "win32").endswith("DueClock")
    assert default_paths({"LOCALAPPDATA": "C:\\L"}, "win32")["config"] == "C:\\L\\DueClock\\connector.json"
    assert "Application Support" in default_dir({"HOME": "/h"}, "darwin")
    assert default_dir({"HOME": "/h"}, "linux").endswith("dueclock")
    assert default_dir({}, "win32")


def test_protector_roundtrip_and_format():
    p = PlainProtector()
    blob = p.protect(KEY)
    assert KEY not in blob and p.unprotect(blob) == KEY and unprotect_any(blob) == KEY
    with pytest.raises(ValueError):
        unprotect_any("garbage")
    assert isinstance(get_protector("linux"), PlainProtector)


def test_setup_writes_protected_key_only(tmp_path):
    cfg = tmp_path / "connector.json"
    res = run_setup(params(), protector=PlainProtector(), config_path=str(cfg), tally_factory=FakeTally, http=http())
    assert res["ok"] and res["checks"]["tally"]["running"] and res["checks"]["cloud"]["ok"]
    text = cfg.read_text()
    assert KEY not in text and "api_key_protected" in text and '"api_key"' not in text
    loaded = ConnectorConfig.load(str(cfg))
    assert loaded.api_key == KEY and loaded.cloud_url == "https://cloud.example.com"
    assert KEY not in json.dumps(res)


def test_setup_rejected_key_writes_nothing(tmp_path):
    cfg = tmp_path / "c.json"
    res = run_setup(params(), protector=PlainProtector(), config_path=str(cfg), tally_factory=FakeTally, http=http(401))
    assert not res["ok"] and not cfg.exists() and "rejected" in res["errors"][0]


def test_setup_invalid_input_and_offline(tmp_path):
    cfg = tmp_path / "c.json"
    assert not run_setup(params(cloud_url="http://evil.example.com"), config_path=str(cfg), tally_factory=FakeTally, http=http())["ok"]
    assert not run_setup(params(api_key="short"), config_path=str(cfg), tally_factory=FakeTally, http=http())["ok"]
    assert not run_setup(params(tally_port="abc"), config_path=str(cfg), tally_factory=FakeTally, http=http())["ok"]
    assert not cfg.exists()

    def boom(r):
        raise httpx.ConnectError("down")
    offline = httpx.Client(transport=httpx.MockTransport(boom))
    res = run_setup(params(), protector=PlainProtector(), config_path=str(cfg), tally_factory=FakeTally, http=offline)
    assert res["ok"] and res["checks"]["cloud"]["ok"] is False and cfg.exists()


def test_params_file_is_wiped(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(params()), encoding="utf-8")
    assert read_params_file(str(f))["api_key"] == KEY
    assert not f.exists()


def test_params_file_with_bom(tmp_path):
    f = tmp_path / "p.json"
    f.write_bytes(b"\xef\xbb\xbf" + json.dumps(params()).encode())
    assert read_params_file(str(f))["cloud_url"].startswith("https")


def test_unreadable_protected_key_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("DUECLOCK_API_KEY", raising=False)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"cloud_url": "https://x.example.com", "api_key_protected": "dpapi:AAAA"}))
    with pytest.raises(ConfigError, match="run setup again"):
        ConnectorConfig.load(str(cfg))


def test_env_key_overrides_protected(tmp_path, monkeypatch):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"cloud_url": "https://x.example.com", "api_key_protected": "dpapi:AAAA"}))
    monkeypatch.setenv("DUECLOCK_API_KEY", KEY)
    assert ConnectorConfig.load(str(cfg)).api_key == KEY


def test_task_xml_is_valid_and_least_privilege():
    xml = task_xml(r"C:\Program Files\x & y\dc.exe", 'run-once --config "C:\\a\\c.json"', interval_minutes=60)
    root = ET.fromstring(xml.split("?>", 1)[1])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:RunLevel", ns).text == "LeastPrivilege"
    assert root.find(".//t:LogonType", ns).text == "InteractiveToken"
    assert root.find(".//t:Interval", ns).text == "PT60M"
    assert root.find(".//t:MultipleInstancesPolicy", ns).text == "IgnoreNew"
    assert root.find(".//t:LogonTrigger", ns) is not None and root.find(".//t:Hidden", ns).text == "true"
    assert root.find(".//t:Command", ns).text == r"C:\Program Files\x & y\dc.exe"
    assert KEY not in xml


def test_action_for_run_frozen_and_dev(tmp_path):
    exe = tmp_path / "dueclock-connectorw.exe"
    exe.write_text("")
    cmd, args = action_for_run(r"C:\c.json", frozen=True, windowed_exe=str(exe))
    assert cmd == str(exe) and args == 'run-once --config "C:\\c.json"'
    cmd, args = action_for_run("/c.json", frozen=False)
    assert cmd == sys.executable and "-m dueclock.connector run-once" in args


def test_cli_version_and_task_xml_only(capsys):
    assert cli.main(["version"]) == 0
    assert "dueclock-connector 0." in capsys.readouterr().out
    assert cli.main(["install-task", "--xml-only", "--config", "/tmp/x.json"]) == 0
    assert "<Task" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform.startswith("win"), reason="non-Windows behaviour")
def test_task_install_refused_off_windows(capsys):
    assert cli.main(["install-task"]) == 1
    assert "only available on Windows" in capsys.readouterr().err


def test_cli_setup_from_file_end_to_end(tmp_path, monkeypatch, capsys):
    pf, cfg = tmp_path / "p.json", tmp_path / "c.json"
    pf.write_text(json.dumps(params()))
    monkeypatch.setattr("dueclock.connector.cli.run_setup", lambda p, config_path=None: run_setup(p, protector=PlainProtector(), config_path=config_path, tally_factory=FakeTally, http=http()))
    assert cli.main(["setup", "--from-file", str(pf), "--config", str(cfg)]) == 0
    out = capsys.readouterr()
    assert KEY not in out.out + out.err and cfg.exists() and not pf.exists()


def test_connector_has_no_web_framework_dependency():
    code = "import sys, dueclock.connector.cli, dueclock.connector.service; bad=[m for m in ('fastapi','starlette','uvicorn') if m in sys.modules]; print(','.join(bad))"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"connector pulls in server packages: {r.stdout}"


def test_packaging_files_hold_the_important_rules():
    iss = open(os.path.join(ROOT, "packaging/windows/installer.iss"), encoding="utf-8").read()
    assert "PrivilegesRequired=lowest" in iss
    assert "setup --from-file" in iss and "--api-key" not in iss
    assert "remove-task" in iss and "install-task" in iss
    spec = open(os.path.join(ROOT, "packaging/windows/dueclock-connector.spec"), encoding="utf-8").read()
    for pkg in ("fastapi", "starlette", "uvicorn", "pytest"):
        assert f'"{pkg}"' in spec
    assert '"pydantic"' not in spec                                                 # the connector validates payloads with it
    assert "dueclock-connectorw" in spec
    reqs = open(os.path.join(ROOT, "requirements-connector.txt"), encoding="utf-8").read()
    assert "httpx" in reqs and "pydantic" in reqs and "fastapi" not in reqs


def test_version_info_generator():
    sys.path.insert(0, os.path.join(ROOT, "packaging", "windows"))
    import make_version_info
    out = make_version_info.render("1.2.3")
    assert "filevers=(1, 2, 3, 0)" in out and "'FileVersion', '1.2.3'" in out


def test_cli_setup_missing_or_bad_answers_file(tmp_path, capsys):
    assert cli.main(["setup", "--from-file", str(tmp_path / "nope.json"), "--config", str(tmp_path / "c.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert cli.main(["setup", "--from-file", str(bad), "--config", str(tmp_path / "c.json")]) == 2
    assert "Traceback" not in capsys.readouterr().err
