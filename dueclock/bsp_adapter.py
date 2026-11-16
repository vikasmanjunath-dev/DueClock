"""
DueClock Meta WhatsApp BSP & Cloud API Dispatcher Adapter
Implements utility template dispatch via Meta Business Cloud API / Business Solution Providers (BSPs).
Includes Mock Test Mode and production rate card tracking (~₹0.145 per utility delivery).
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field
import requests


class MessageDeliveryStatus(str, Enum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"


class WhatsAppDispatchRecord(BaseModel):
    message_id: str
    recipient_mobile: str
    template_name: str
    language_code: str
    parameters: List[str]
    delivery_status: MessageDeliveryStatus = MessageDeliveryStatus.QUEUED
    bsp_vendor: str = "META_DIRECT"      # "META_DIRECT", "GUPSHUP", or "MOCK_SANDBOX"
    cost_in_inr: float = 0.145           # Standard Indian utility template rate
    dispatched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: Optional[str] = None


class WhatsAppBSPAdapter:
    """
    Adapter for dispatching WhatsApp utility reminders via Meta Cloud API or partner BSPs.
    """

    def __init__(
        self,
        api_token: str = "mock_meta_access_token",
        phone_number_id: str = "mock_phone_number_id",
        use_mock: bool = True
    ):
        self.api_token = api_token
        self.phone_number_id = phone_number_id
        self.use_mock = use_mock
        self.dispatch_log: List[WhatsAppDispatchRecord] = []

    def dispatch_utility_reminder(
        self,
        recipient_mobile: str,
        template_name: str,
        language_code: str,
        parameters: List[str]
    ) -> WhatsAppDispatchRecord:
        """Dispatches utility template reminder."""
        msg_id = f"wamid_dc_{len(self.dispatch_log) + 1}_{int(datetime.now().timestamp())}"

        if self.use_mock:
            record = WhatsAppDispatchRecord(
                message_id=msg_id,
                recipient_mobile=recipient_mobile,
                template_name=template_name,
                language_code=language_code,
                parameters=parameters,
                delivery_status=MessageDeliveryStatus.DELIVERED,
                bsp_vendor="MOCK_SANDBOX",
                cost_in_inr=0.145
            )
            self.dispatch_log.append(record)
            return record

        # Production Meta Cloud API endpoint
        url = f"https://graph.facebook.com/v18.0/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        body = {
            "messaging_product": "whatsapp",
            "to": recipient_mobile.replace("+", "").replace(" ", ""),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": p} for p in parameters]
                    }
                ]
            }
        }

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=5)
            if resp.status_code in [200, 201]:
                data = resp.json()
                meta_id = data.get("messages", [{}])[0].get("id", msg_id)
                record = WhatsAppDispatchRecord(
                    message_id=meta_id,
                    recipient_mobile=recipient_mobile,
                    template_name=template_name,
                    language_code=language_code,
                    parameters=parameters,
                    delivery_status=MessageDeliveryStatus.SENT,
                    bsp_vendor="META_DIRECT",
                    cost_in_inr=0.145
                )
            else:
                record = WhatsAppDispatchRecord(
                    message_id=msg_id,
                    recipient_mobile=recipient_mobile,
                    template_name=template_name,
                    language_code=language_code,
                    parameters=parameters,
                    delivery_status=MessageDeliveryStatus.FAILED,
                    bsp_vendor="META_DIRECT",
                    error_message=f"HTTP {resp.status_code}: {resp.text}"
                )
        except Exception as e:
            record = WhatsAppDispatchRecord(
                message_id=msg_id,
                recipient_mobile=recipient_mobile,
                template_name=template_name,
                language_code=language_code,
                parameters=parameters,
                delivery_status=MessageDeliveryStatus.FAILED,
                error_message=str(e)
            )

        self.dispatch_log.append(record)
        return record
