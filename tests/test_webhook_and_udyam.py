"""
Unit tests for WhatsApp Webhook Intent Classifier and Udyam Verification Adapter
"""

import pytest
from datetime import date
from dueclock.whatsapp_webhook import WhatsAppWebhookManager, BuyerIntent
from dueclock.udyam_adapter import UdyamAdapter
from dueclock.models import EnterpriseCategory, EnterpriseActivity


def test_meta_webhook_challenge():
    manager = WhatsAppWebhookManager(webhook_verify_token="test_secret_token")
    challenge = manager.verify_webhook_subscription("subscribe", "test_secret_token", "123456789")
    assert challenge == "123456789"

    # Invalid token rejected
    bad = manager.verify_webhook_subscription("subscribe", "wrong_token", "123456789")
    assert bad is None


def test_classify_promise_to_pay():
    manager = WhatsAppWebhookManager()
    text = "We will arrange payment this Friday"
    res = manager.classify_reply(text)

    assert res.intent == BuyerIntent.PROMISE_TO_PAY
    assert res.confidence_score >= 0.90
    assert res.extracted_date is not None
    assert res.action_taken == "CADENCE_PAUSED_UNTIL_DATE"


def test_classify_quality_dispute():
    manager = WhatsAppWebhookManager()
    text = "Batch received has severe defect and defective surface finish, material rejected."
    res = manager.classify_reply(text)

    assert res.intent == BuyerIntent.DISPUTE_QUALITY
    assert res.confidence_score >= 0.90
    assert res.action_taken == "CADENCE_HALTED_FOR_DISPUTE"


def test_classify_payment_completed_with_utr():
    manager = WhatsAppWebhookManager()
    text = "Payment done via NEFT. UTR: CITIN261107998822. Please check and confirm."
    res = manager.classify_reply(text)

    assert res.intent == BuyerIntent.PAYMENT_COMPLETED
    assert res.confidence_score >= 0.90
    assert res.extracted_utr == "CITIN261107998822"


def test_udyam_format_validation():
    adapter = UdyamAdapter(cache_file_path="/tmp/test_udyam_cache.json")
    
    assert adapter.validate_urn_format("UDYAM-KR-03-0012345") is True
    assert adapter.validate_urn_format("UDYAM-MH-01-0000001") is True
    assert adapter.validate_urn_format("INVALID-FORMAT") is False
    assert adapter.validate_urn_format("1234567890123456789") is False


def test_udyam_trader_exclusion(tmp_path):
    cache_file = str(tmp_path / "cache.json")
    adapter = UdyamAdapter(cache_file_path=cache_file)
    
    res = adapter.verify_udyam(
        udyam_number="UDYAM-DL-01-0099999",
        declared_category=EnterpriseCategory.SMALL,
        declared_activity=EnterpriseActivity.TRADING,
        enterprise_name="Delhi Electrical Traders"
    )

    assert res.is_valid is True
    assert res.is_43bh_covered is False
    assert "Trading NIC code" in res.legal_note


def test_udyam_caching(tmp_path):
    cache_file = str(tmp_path / "cache.json")
    adapter = UdyamAdapter(cache_file_path=cache_file)
    urn = "UDYAM-KR-03-0077777"
    
    res1 = adapter.verify_udyam(urn, EnterpriseCategory.MICRO, EnterpriseActivity.MANUFACTURING)
    assert res1.is_cached is False

    res2 = adapter.verify_udyam(urn)
    assert res2.is_cached is True
    assert res2.validation_source == "PERSISTENT_CACHE"
