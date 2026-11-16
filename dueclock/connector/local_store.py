"""Durable local state: the upload queue, the last-sent snapshot, and run history (SQLite).

The queue survives restarts and power cuts. Batches retry with exponential backoff and jitter and move to
'dead' after too many attempts. The snapshot lets the connector send only what changed and notice bills that
have vanished from Tally (settled, or something wrong).
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

BACKOFF_BASE_S = 30
BACKOFF_CAP_S = 6 * 3600


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class LocalStore:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            for stmt in (
                "CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT, records INTEGER, attempts INTEGER DEFAULT 0, "
                "next_attempt_at TEXT, status TEXT, last_error TEXT)",
                "CREATE TABLE IF NOT EXISTS snapshot (company TEXT, key TEXT, record TEXT, record_hash TEXT, pending REAL, PRIMARY KEY (company, key))",
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT, detail TEXT)",
                "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)",
            ):
                self.conn.execute(stmt)
            self.conn.commit()

    # ---- queue
    def enqueue(self, payload: Dict[str, Any], now: Optional[datetime] = None) -> str:
        now = now or utcnow()
        bid = uuid.uuid4().hex
        with self.lock:
            self.conn.execute("INSERT INTO batches VALUES (?,?,?,?,0,?,'pending',NULL)",
                              (bid, iso(now), json.dumps(payload), len(payload.get("records", [])), iso(now)))
            self.conn.commit()
        return bid

    def due_batches(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now or utcnow()
        with self.lock:
            rows = self.conn.execute("SELECT * FROM batches WHERE status='pending' AND next_attempt_at <= ? ORDER BY created_at", (iso(now),)).fetchall()
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]

    def mark_sent(self, batch_id: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE batches SET status='sent', payload='{}', last_error=NULL WHERE id=?", (batch_id,))
            self.conn.commit()

    def mark_failed(self, batch_id: str, error: str, max_attempts: int, now: Optional[datetime] = None,
                    retry_after_s: Optional[float] = None, rng: Optional[random.Random] = None) -> str:
        now = now or utcnow()
        rng = rng or random
        with self.lock:
            attempts = self.conn.execute("SELECT attempts FROM batches WHERE id=?", (batch_id,)).fetchone()["attempts"] + 1
            if attempts >= max_attempts:
                self.conn.execute("UPDATE batches SET status='dead', attempts=?, last_error=? WHERE id=?", (attempts, error[:300], batch_id))
                self.conn.commit()
                return "dead"
            delay = retry_after_s if retry_after_s is not None else min(BACKOFF_BASE_S * (2 ** (attempts - 1)), BACKOFF_CAP_S)
            delay *= 1 + rng.uniform(0, 0.1)
            self.conn.execute("UPDATE batches SET attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
                              (attempts, iso(now + timedelta(seconds=delay)), error[:300], batch_id))
            self.conn.commit()
            return "pending"

    def mark_dead(self, batch_id: str, error: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE batches SET status='dead', last_error=? WHERE id=?", (error[:300], batch_id))
            self.conn.commit()

    def replace_batch(self, batch_id: str, payloads: List[Dict[str, Any]], now: Optional[datetime] = None) -> None:
        """Swap one batch for smaller ones (used when the server says the payload is too large)."""
        with self.lock:
            self.conn.execute("DELETE FROM batches WHERE id=?", (batch_id,))
            self.conn.commit()
        for p in payloads:
            self.enqueue(p, now)

    def queue_stats(self) -> Dict[str, int]:
        with self.lock:
            rows = self.conn.execute("SELECT status, COUNT(*) AS n, COALESCE(SUM(records),0) AS r FROM batches GROUP BY status").fetchall()
        out = {"pending": 0, "sent": 0, "dead": 0, "pending_records": 0}
        for r in rows:
            out[r["status"]] = r["n"]
            if r["status"] == "pending":
                out["pending_records"] = r["r"]
        return out

    def purge_sent(self, older_than_days: int = 7, now: Optional[datetime] = None) -> int:
        cutoff = iso((now or utcnow()) - timedelta(days=older_than_days))
        with self.lock:
            n = self.conn.execute("DELETE FROM batches WHERE status='sent' AND created_at < ?", (cutoff,)).rowcount
            self.conn.commit()
        return n

    # ---- snapshot
    def snapshot(self, company: str) -> Dict[str, Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT key, record, record_hash, pending FROM snapshot WHERE company=?", (company,)).fetchall()
        return {r["key"]: {"record": json.loads(r["record"]), "hash": r["record_hash"], "pending": r["pending"]} for r in rows}

    def save_snapshot(self, company: str, entries: Dict[str, Dict[str, Any]]) -> None:
        with self.lock:
            for key, e in entries.items():
                self.conn.execute("INSERT OR REPLACE INTO snapshot VALUES (?,?,?,?,?)",
                                  (company, key, json.dumps(e["record"]), e["hash"], e["pending"]))
            self.conn.commit()

    # ---- runs and kv
    def record_run(self, started: datetime, finished: datetime, status: str, detail: Dict[str, Any]) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (uuid.uuid4().hex, iso(started), iso(finished), status, json.dumps(detail)))
            self.conn.commit()

    def last_runs(self, n: int = 10) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM runs ORDER BY finished_at DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) | {"detail": json.loads(r["detail"])} for r in rows]

    def set(self, k: str, v: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, v))
            self.conn.commit()

    def get(self, k: str, default: Optional[str] = None) -> Optional[str]:
        with self.lock:
            r = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def close(self) -> None:
        self.conn.close()
