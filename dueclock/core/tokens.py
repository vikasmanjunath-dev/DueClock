"""Signed, expiring link tokens for the buyer page.

A token proves the link was issued by us (HMAC-SHA256), names the customer and the link record, and carries an
expiry. It holds no personal data. Revocation and 'one invoice only' are enforced against the link record in
the customer's database, so a leaked token cannot be widened to another invoice.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from typing import Optional

PREFIX = "v1"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass(frozen=True)
class Claims:
    tenant_id: str
    link_id: str
    expires_at: int


class TokenSigner:
    def __init__(self, secret: bytes):
        if len(secret) < 32:
            raise ValueError("token secret must be at least 32 bytes")
        self._secret = secret

    def issue(self, tenant_id: str, link_id: str, expires_at: int) -> str:
        body = _b64(json.dumps({"t": tenant_id, "l": link_id, "e": int(expires_at)}, separators=(",", ":")).encode())
        sig = hmac.new(self._secret, f"{PREFIX}.{body}".encode(), hashlib.sha256).digest()
        return f"{PREFIX}.{body}.{_b64(sig)}"

    def verify(self, token: str, now_epoch: int) -> Optional[Claims]:
        try:
            prefix, body, sig = token.split(".")
            if prefix != PREFIX:
                return None
            want = hmac.new(self._secret, f"{prefix}.{body}".encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(want, _unb64(sig)):
                return None
            d = json.loads(_unb64(body))
            c = Claims(str(d["t"]), str(d["l"]), int(d["e"]))
        except Exception:
            return None
        return c if c.expires_at > now_epoch else None


def load_secret(data_dir: str) -> bytes:
    """Secret from DUECLOCK_TOKEN_SECRET, else a random file created once in the data directory (mode 600)."""
    env = os.environ.get("DUECLOCK_TOKEN_SECRET")
    if env:
        return hashlib.sha256(env.encode()).digest()
    path = os.path.join(data_dir, "token_secret")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    os.makedirs(data_dir, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


class SessionSigner:
    """Signed, expiring owner-session cookies plus a per-session CSRF token. Separate key from the buyer-link key."""

    def __init__(self, secret: bytes):
        self._k = hashlib.sha256(b"owner-session" + secret).digest()

    def _sig(self, msg: str) -> str:
        return _b64(hmac.new(self._k, msg.encode(), hashlib.sha256).digest())

    def issue(self, tenant_id: str, expires_at: int) -> str:
        body = _b64(json.dumps({"t": tenant_id, "e": int(expires_at), "n": secrets.token_hex(8)}, separators=(",", ":")).encode())
        return f"{body}.{self._sig(body)}"

    def verify(self, value: Optional[str], now_epoch: int) -> Optional[tuple]:
        try:
            body, sig = (value or "").split(".")
            if not hmac.compare_digest(sig, self._sig(body)):
                return None
            d = json.loads(_unb64(body))
            if int(d["e"]) <= now_epoch:
                return None
            return str(d["t"]), str(d["n"])
        except Exception:
            return None

    def csrf(self, nonce: str) -> str:
        return hmac.new(self._k, b"csrf" + nonce.encode(), hashlib.sha256).hexdigest()[:32]
