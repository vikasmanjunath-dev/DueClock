"""Entry point for the frozen Windows executables (console: dueclock-connector.exe, windowed: dueclock-connectorw.exe)."""
import os
import sys


def _ensure_streams() -> None:
    """The windowed build has no console: sys.stdout/stderr are None. Send them to the log file so nothing crashes on print()."""
    if sys.stdout is None or sys.stderr is None:
        from dueclock.connector.paths import default_paths
        p = default_paths()
        os.makedirs(p["dir"], exist_ok=True)
        f = open(p["log"], "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


if __name__ == "__main__":
    _ensure_streams()
    from dueclock.connector.cli import main
    sys.exit(main())
