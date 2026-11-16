"""First-time setup: validate the inputs, check Tally and the cloud, write the config with the key protected, never print the key."""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional

import httpx

from .config import ConfigError, ConnectorConfig
from .paths import default_paths
from .protect import Protector, get_protector
from .tally_client import TallyClient


def run_setup(params: Dict[str, Any], *, protector: Optional[Protector] = None, config_path: Optional[str] = None,
              tally_factory: Callable[..., TallyClient] = TallyClient, http: Optional[httpx.Client] = None) -> Dict[str, Any]:
    """Returns {"ok", "config_path", "checks": {...}, "errors": [...]}. Fails (ok False, nothing written) on invalid input or a rejected key."""
    protector = protector or get_protector()
    paths = default_paths()
    config_path = config_path or paths["config"]
    out: Dict[str, Any] = {"ok": False, "config_path": config_path, "checks": {}, "errors": []}
    try:
        cfg = ConnectorConfig(
            cloud_url=str(params.get("cloud_url", "")).strip(), api_key=str(params.get("api_key", "")).strip(), tally_host=params.get("tally_host", "127.0.0.1"),
            tally_port=int(params.get("tally_port", 9000) or 9000), companies=list(params.get("companies") or []),
            state_path=params.get("state_path") or paths["state"], log_path=params.get("log_path") or paths["log"]).validate()
    except (ConfigError, ValueError) as e:
        out["errors"].append(str(e))
        return out

    probe = tally_factory(cfg.tally_host, cfg.tally_port, 5.0).probe()
    out["checks"]["tally"] = {"running": probe.running, "product": probe.product}
    client = http or httpx.Client(timeout=15.0)
    try:
        r = client.get(cfg.cloud_url.rstrip("/") + "/v1/connector/ping", headers={"X-API-Key": cfg.api_key})
        if r.status_code in (401, 403):
            out["errors"].append("The cloud rejected this access key. Check it was copied completely.")
            out["checks"]["cloud"] = {"ok": False, "error": "key rejected"}
            return out
        out["checks"]["cloud"] = {"ok": r.status_code == 200, "tenant": r.json().get("tenant") if r.status_code == 200 else None}
    except httpx.HTTPError as e:
        out["checks"]["cloud"] = {"ok": False, "error": type(e).__name__}              # offline during install is allowed; the queue will catch up
    data = {k: v for k, v in cfg.__dict__.items() if k != "api_key"}
    data["api_key_protected"] = protector.protect(cfg.api_key)
    os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass
    out["ok"] = True
    return out


def read_params_file(path: str) -> Dict[str, Any]:
    """The installer hands over its answers in a temp file (not on the command line, where other programs can read it). It is wiped after reading."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    finally:
        try:
            size = os.path.getsize(path)
            with open(path, "wb") as f:
                f.write(b"\0" * size)
            os.remove(path)
        except OSError:
            pass
    return data
