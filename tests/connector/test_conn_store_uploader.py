import random
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dueclock.connector.config import ConnectorConfig
from dueclock.connector.local_store import LocalStore
from dueclock.connector.uploader import AuthRejected, Uploader

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
KEY = "dc_" + "y" * 40


def payload(n=3):
    return {"connector_version": "0.1.0", "records": [{"invoice_number": f"I{i}"} for i in range(n)]}


def test_queue_survives_restart(tmp_path):
    path = str(tmp_path / "s.db")
    s = LocalStore(path)
    bid = s.enqueue(payload(), T0)
    s.close()
    s2 = LocalStore(path)
    due = s2.due_batches(T0)
    assert [b["id"] for b in due] == [bid] and due[0]["records"] == 3 and len(due[0]["payload"]["records"]) == 3


def test_backoff_grows_and_dead_letters():
    s = LocalStore(":memory:")
    bid = s.enqueue(payload(), T0)
    rng = random.Random(1)
    delays = []
    for i in range(3):
        assert s.mark_failed(bid, "x", 5, T0, rng=rng) == "pending"
        row = s.conn.execute("SELECT next_attempt_at FROM batches WHERE id=?", (bid,)).fetchone()
        delays.append(datetime.fromisoformat(row["next_attempt_at"]) - T0)
    assert delays[0] < delays[1] < delays[2] and delays[0].total_seconds() >= 30
    assert s.due_batches(T0) == [] and len(s.due_batches(T0 + timedelta(days=1))) == 1
    s.mark_failed(bid, "x", 5, T0); assert s.mark_failed(bid, "x", 5, T0) == "dead"
    assert s.queue_stats()["dead"] == 1 and s.due_batches(T0 + timedelta(days=30)) == []


def test_replace_batch_and_stats_and_purge():
    s = LocalStore(":memory:")
    bid = s.enqueue(payload(4), T0)
    s.replace_batch(bid, [payload(2), payload(2)], T0)
    assert s.queue_stats()["pending"] == 2 and s.queue_stats()["pending_records"] == 4
    for b in s.due_batches(T0):
        s.mark_sent(b["id"])
    assert s.purge_sent(7, T0 + timedelta(days=8)) == 2


def test_snapshot_roundtrip():
    s = LocalStore(":memory:")
    s.save_snapshot("A", {"k1": {"record": {"a": 1}, "hash": "h", "pending": 5.0}})
    assert s.snapshot("A")["k1"]["record"] == {"a": 1} and s.snapshot("B") == {}


class Scripted:
    """Stands in for httpx.Client: returns scripted responses in order."""
    def __init__(self, *items):
        self.items, self.calls = list(items), 0

    def post(self, url, json=None, headers=None):
        self.calls += 1
        it = self.items.pop(0)
        if isinstance(it, Exception):
            raise it
        return it

    def get(self, url, headers=None):
        return self.items.pop(0)


def uploader(client, store=None, **kw):
    cfg = ConnectorConfig(cloud_url="https://api.example.com", api_key=KEY, **kw).validate()
    return Uploader(cfg, store or LocalStore(":memory:"), client), cfg


def test_success_marks_sent_and_counts_record_errors():
    st = LocalStore(":memory:"); st.enqueue(payload(), T0)
    u, _ = uploader(Scripted(httpx.Response(200, json={"errors": [{"x": 1}]})), st)
    out = u.flush(T0)
    assert (out.sent, out.records_sent, out.record_errors) == (1, 3, 1) and st.queue_stats()["sent"] == 1


def test_auth_rejection_stops_and_keeps_batch():
    st = LocalStore(":memory:"); st.enqueue(payload(), T0)
    u, _ = uploader(Scripted(httpx.Response(401, json={})), st)
    with pytest.raises(AuthRejected):
        u.flush(T0)
    assert st.queue_stats()["pending"] == 1


def test_413_splits_then_sends():
    st = LocalStore(":memory:"); st.enqueue(payload(4), T0)
    u, _ = uploader(Scripted(httpx.Response(413), httpx.Response(200, json={}), httpx.Response(200, json={})), st)
    first = u.flush(T0)
    assert first.split == 1 and st.queue_stats()["pending"] == 2
    second = u.flush(T0)
    assert second.sent == 2 and second.records_sent == 4


@pytest.mark.parametrize("resp", [httpx.Response(500), httpx.Response(503), httpx.Response(429, headers={"Retry-After": "120"}), httpx.ConnectError("down")])
def test_retryable_failures_back_off(resp):
    st = LocalStore(":memory:"); st.enqueue(payload(), T0)
    u, _ = uploader(Scripted(resp), st)
    out = u.flush(T0)
    assert out.retried == 1 and st.queue_stats()["pending"] == 1 and st.due_batches(T0) == []


def test_bad_payload_is_dead_lettered_not_retried():
    st = LocalStore(":memory:"); st.enqueue(payload(), T0)
    u, _ = uploader(Scripted(httpx.Response(422, text="bad")), st)
    assert u.flush(T0).dead == 1 and st.queue_stats()["dead"] == 1


def test_gives_up_after_max_attempts():
    st = LocalStore(":memory:"); st.enqueue(payload(), T0)
    u, _ = uploader(Scripted(*[httpx.Response(500)] * 3), st, max_attempts=3)
    now = T0
    for _ in range(3):
        u.flush(now); now += timedelta(days=1)
    assert st.queue_stats()["dead"] == 1
