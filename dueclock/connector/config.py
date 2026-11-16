"""Connector configuration: a JSON file, with the API key optionally from the environment. The key is never logged."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


class ConfigError(ValueError):
    pass


@dataclass
class ConnectorConfig:
    cloud_url: str
    api_key: str
    tally_host: str = "127.0.0.1"
    tally_port: int = 9000
    companies: List[str] = field(default_factory=list)       # empty = the company currently open in Tally
    state_path: str = "connector_state.db"
    schedule_hour: int = 2
    schedule_minute: int = 0
    batch_size: int = 500
    tally_timeout_s: float = 20.0
    cloud_timeout_s: float = 30.0
    max_attempts: int = 12
    mass_settlement_ratio: float = 0.5                          # hold if more than this share of open bills vanish at once
    mass_settlement_min: int = 5                                # ...and at least this many
    log_path: str = ""
    tally_profile: Dict[str, str] = field(default_factory=dict)   # request variants that worked on this Tally (written by tally-check)
    tally_bills_template_path: str = ""                          # optional on-site request file (read-only Export envelope; {company} placeholder)

    def validate(self) -> "ConnectorConfig":
        u = urlparse(self.cloud_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ConfigError("cloud_url must be a full http(s) URL")
        if u.scheme == "http" and u.hostname not in LOCAL_HOSTS:
            raise ConfigError("cloud_url must use https unless it is a local address")
        if not self.api_key or not self.api_key.startswith("dc_") or len(self.api_key) < 20:
            raise ConfigError("api_key missing or malformed (expected the key issued for this customer)")
        if not (1 <= self.tally_port <= 65535):
            raise ConfigError("tally_port out of range")
        if not (0 <= self.schedule_hour <= 23 and 0 <= self.schedule_minute <= 59):
            raise ConfigError("schedule time invalid")
        if not (1 <= self.batch_size <= 5000):
            raise ConfigError("batch_size must be between 1 and 5000")
        if not (0 < self.mass_settlement_ratio <= 1):
            raise ConfigError("mass_settlement_ratio must be in (0, 1]")
        from .tally_client import BILLS_VARIANTS, COMPANY_VARIANTS
        if self.tally_profile.get("bills", BILLS_VARIANTS[0]) not in BILLS_VARIANTS or self.tally_profile.get("companies", COMPANY_VARIANTS[0]) not in COMPANY_VARIANTS:
            raise ConfigError("tally_profile names an unknown request variant; run tally-check again")
        if any(not isinstance(c, str) or not c.strip() for c in self.companies):
            raise ConfigError("companies must be non-empty names")
        return self

    def redacted(self) -> Dict[str, Any]:
        d = asdict(self)
        d["api_key"] = self.api_key[:6] + "…" if self.api_key else ""
        return d

    @classmethod
    def load(cls, path: str) -> "ConnectorConfig":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        key = os.environ.get("DUECLOCK_API_KEY") or raw.get("api_key", "")
        if not key and raw.get("api_key_protected"):
            from .protect import unprotect_any
            try:
                key = unprotect_any(raw["api_key_protected"])
            except Exception as e:
                raise ConfigError("the stored access key cannot be read on this computer or by this user; run setup again") from e
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__ and k != "api_key"}
        return cls(api_key=key, **known).validate()
