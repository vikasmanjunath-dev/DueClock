"""Message senders: a mock for tests and a WhatsApp Cloud API sender (Meta directly or a BSP with the same payloads).

Error mapping to retry classes is from Meta's documented error codes as I recall them [A]: verify against the
provider's current documentation before the first live send.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import httpx

RETRYABLE_CODES = {130429, 131056, 131000, 2, 4, 17, 80007}       # throughput / pair rate limit / temporary
FATAL_CODES = {190, 10, 200}                                      # token expired or lacks permission
TEMPLATE_FAULT_CODES = {132000, 132001, 132005, 132007, 132012, 132015, 132016}   # template missing, paused or mismatched


@dataclass(frozen=True)
class SendResult:
    ok: bool
    provider_id: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False
    fatal: bool = False


class MessageSender(Protocol):
    def send_template(self, *, to: str, template: str, language: str, body_vars: List[str], button_suffix: Optional[str]) -> SendResult: ...


def normalize_mobile(raw: Optional[str]) -> Optional[str]:
    """Digits with country code, e.g. '+91 98450 12345' -> '919845012345'. Indian mobiles only for now."""
    if not raw:
        return None
    d = re.sub(r"\D", "", raw)
    if len(d) == 10 and d[0] in "6789":
        return "91" + d
    if len(d) == 11 and d[0] == "0" and d[1] in "6789":
        return "91" + d[1:]
    if len(d) == 12 and d.startswith("91") and d[2] in "6789":
        return d
    return None


@dataclass
class MockSender:
    """Records every send. ``script`` is a queue of SendResult or Exception returned/raised in order."""
    sent: List[Dict[str, Any]] = field(default_factory=list)
    script: List[Any] = field(default_factory=list)
    counter: int = 0

    def send_template(self, *, to: str, template: str, language: str, body_vars: List[str], button_suffix: Optional[str]) -> SendResult:
        self.sent.append({"to": to, "template": template, "language": language, "vars": list(body_vars), "button": button_suffix})
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        self.counter += 1
        return SendResult(True, provider_id=f"wamid.mock.{self.counter}")


class CloudApiSender:
    def __init__(self, phone_number_id: str, access_token: str, api_version: str = "v21.0", base_url: str = "https://graph.facebook.com",
                 client: Optional[httpx.Client] = None, timeout: float = 15.0):
        self.url = f"{base_url.rstrip('/')}/{api_version}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        self.client = client or httpx.Client(timeout=timeout)

    @staticmethod
    def payload(to: str, template: str, language: str, body_vars: List[str], button_suffix: Optional[str]) -> Dict[str, Any]:
        comps: List[Dict[str, Any]] = [{"type": "body", "parameters": [{"type": "text", "text": str(v)} for v in body_vars]}]
        if button_suffix:
            comps.append({"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": button_suffix}]})
        return {"messaging_product": "whatsapp", "to": to, "type": "template",
                "template": {"name": template, "language": {"code": language}, "components": comps}}

    def send_template(self, *, to: str, template: str, language: str, body_vars: List[str], button_suffix: Optional[str]) -> SendResult:
        try:
            r = self.client.post(self.url, json=self.payload(to, template, language, body_vars, button_suffix), headers=self._headers)
        except httpx.HTTPError as e:
            return SendResult(False, error=f"network: {type(e).__name__}", retryable=True)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 200 and data.get("messages"):
            return SendResult(True, provider_id=data["messages"][0].get("id"))
        err = data.get("error", {}) if isinstance(data, dict) else {}
        code, msg = err.get("code"), str(err.get("message", ""))[:160]
        text = f"HTTP {r.status_code} code {code}: {msg}".strip()
        if r.status_code in (401, 403) or code in FATAL_CODES:
            return SendResult(False, error=text, fatal=True)
        if r.status_code == 429 or r.status_code >= 500 or code in RETRYABLE_CODES:
            return SendResult(False, error=text, retryable=True)
        return SendResult(False, error=text)          # permanent: bad number, template fault, policy


def sender_from_env() -> Optional[MessageSender]:
    """DUECLOCK_WA_MODE=cloud with DUECLOCK_WA_PHONE_NUMBER_ID and DUECLOCK_WA_TOKEN (optional DUECLOCK_WA_BASE_URL, _API_VERSION)."""
    if os.environ.get("DUECLOCK_WA_MODE", "").lower() != "cloud":
        return None
    pid, token = os.environ.get("DUECLOCK_WA_PHONE_NUMBER_ID"), os.environ.get("DUECLOCK_WA_TOKEN")
    if not pid or not token:
        return None
    return CloudApiSender(pid, token, os.environ.get("DUECLOCK_WA_API_VERSION", "v21.0"), os.environ.get("DUECLOCK_WA_BASE_URL", "https://graph.facebook.com"))
