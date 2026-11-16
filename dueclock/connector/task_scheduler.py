"""Windows Task Scheduler integration: run the connector at sign-in and every hour, quietly, as the signed-in user.

The task runs `run-once`: it reads Tally, queues changes and flushes the queue. The queue and change detection live on disk,
so hourly runs are cheap and nothing is lost if one is missed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime
from typing import List, Optional
from xml.sax.saxutils import escape

TASK_NAME = "DueClock Connector"


def task_xml(command: str, arguments: str, *, interval_minutes: int = 60, start: Optional[datetime] = None, description: str = "Sends Tally receivables to DueClock (read-only).") -> str:
    start = start or datetime.now()
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape(description)}</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><Delay>PT2M</Delay></LogonTrigger>
    <TimeTrigger>
      <StartBoundary>{start.strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary>
      <Repetition><Interval>PT{int(interval_minutes)}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT5M</Interval><Count>3</Count></RestartOnFailure>
    <Hidden>true</Hidden>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(command)}</Command><Arguments>{escape(arguments)}</Arguments></Exec></Actions>
</Task>
"""


def _quote(p: str) -> str:
    return f'"{p}"'


def action_for_run(config_path: str, frozen: Optional[bool] = None, windowed_exe: Optional[str] = None) -> tuple:
    """(command, arguments) for the scheduled task. A frozen build uses the windowed exe so no console flashes up."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        exe = windowed_exe or os.path.join(os.path.dirname(sys.executable), "dueclock-connectorw.exe")
        if not os.path.exists(exe):
            exe = sys.executable
        return exe, f"run-once --config {_quote(config_path)}"
    return sys.executable, f"-m dueclock.connector run-once --config {_quote(config_path)}"


def _run(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def install_task(command: str, arguments: str, interval_minutes: int = 60) -> str:
    if not sys.platform.startswith("win"):
        raise OSError("Task Scheduler is only available on Windows")
    xml = task_xml(command, arguments, interval_minutes=interval_minutes)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
        f.write(xml)
        path = f.name
    try:
        r = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"])
    finally:
        os.unlink(path)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout or "schtasks failed").strip()[:300])
    return "installed"


def remove_task() -> str:
    if not sys.platform.startswith("win"):
        raise OSError("Task Scheduler is only available on Windows")
    r = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    return "removed" if r.returncode == 0 else "not found"


def task_status() -> str:
    if not sys.platform.startswith("win"):
        return "n/a"
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    return "installed" if r.returncode == 0 else "missing"
