"""Where the connector keeps its files. Per user, no administrator rights: Tally runs in the user's session, and so does the connector."""
from __future__ import annotations

import ntpath
import os
import posixpath
import sys
from typing import Dict, Mapping, Optional


def default_dir(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> str:
    env, platform = env if env is not None else os.environ, platform or sys.platform
    if platform.startswith("win"):
        j = ntpath.join
        return j(env.get("LOCALAPPDATA") or j(env.get("USERPROFILE", "C:\\Users\\Default"), "AppData", "Local"), "DueClock")
    j = posixpath.join
    if platform == "darwin":
        return j(env.get("HOME", "~"), "Library", "Application Support", "DueClock")
    return j(env.get("XDG_DATA_HOME") or j(env.get("HOME", "~"), ".local", "share"), "dueclock")


def default_paths(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> Dict[str, str]:
    base = default_dir(env, platform)
    sep = "\\" if (platform or sys.platform).startswith("win") else "/"
    return {"dir": base, "config": base + sep + "connector.json", "state": base + sep + "connector_state.db", "log": base + sep + "connector.log"}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
