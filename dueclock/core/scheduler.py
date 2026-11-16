"""Optional in-process reminder scheduler (DUECLOCK_SCHEDULER=1), so no host-specific cron job is needed.

Every ``interval`` seconds it runs the reminder engine for each active customer. Running it on several servers at once, or twice,
is safe: a reminder step is claimed by a UNIQUE database row before anything is sent, so the second runner finds it already claimed.
One customer's failure never stops the others.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Dict, Optional

log = logging.getLogger("dueclock.scheduler")


class ReminderScheduler:
    def __init__(self, control: Any, engine: Any, interval_s: int = 3600, first_delay_s: int = 60):
        self.control, self.engine = control, engine
        self.interval_s, self.first_delay_s = max(60, int(interval_s)), max(0, int(first_delay_s))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last: Dict[str, Any] = {}

    def run_once(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"tenants": 0, "sent": 0, "failed": 0, "errors": 0}
        for t in self.control.list_tenants():
            if not t["active"]:
                continue
            summary["tenants"] += 1
            try:
                res = self.engine.run(self.control.store_for(self.control.context_by_id(t["id"])))
                summary["sent"] += getattr(res, "sent", 0)
                summary["failed"] += getattr(res, "failed", 0)
            except Exception:                                                  # noqa: BLE001 - isolate customers from each other
                summary["errors"] += 1
                log.exception(f"reminder run failed for tenant {t['id']}")
        self.last = summary
        log.info(f"reminder run: {summary}")           # pre-formatted: another component installs a log filter that rewrites record.args
        return summary

    def _loop(self) -> None:
        if self._stop.wait(self.first_delay_s):
            return
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:                                                  # noqa: BLE001
                log.exception("scheduler loop error")
            if self._stop.wait(self.interval_s):
                return

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="dueclock-reminders", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever sys.stdout is at the moment of logging (stays correct if stdout is redirected or replaced)."""
    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self):                                          # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:
        pass


def _ensure_visible_logging() -> None:
    """uvicorn only configures its own loggers: without this the scheduler's INFO lines (and its error reports) would be invisible in the host's logs."""
    base = logging.getLogger("dueclock")
    if not any(getattr(h, "_dueclock", False) for h in base.handlers):
        h = _StdoutHandler()
        h.setFormatter(logging.Formatter("%(levelname)s:     dueclock %(name)s: %(message)s"))
        h._dueclock = True                                     # type: ignore[attr-defined]
        base.addHandler(h)
    if base.level == logging.NOTSET or base.level > logging.INFO:
        base.setLevel(logging.INFO)


def attach(app: Any, env: Any) -> Optional[ReminderScheduler]:
    """Wire the scheduler into an app's startup/shutdown when DUECLOCK_SCHEDULER=1 (DUECLOCK_SCHEDULER_MINUTES sets the interval, default 60)."""
    if str(env.get("DUECLOCK_SCHEDULER", "")).lower() not in ("1", "true", "yes"):
        return None
    s = ReminderScheduler(app.state.control, app.state.engine, interval_s=int(env.get("DUECLOCK_SCHEDULER_MINUTES", "60")) * 60)
    _ensure_visible_logging()

    def start() -> None:
        log.info(f"reminder scheduler started: first run in {s.first_delay_s}s, then every {s.interval_s // 60} min")
        s.start()
    app.add_event_handler("startup", start)
    app.add_event_handler("shutdown", s.stop)
    app.state.scheduler = s
    return s
