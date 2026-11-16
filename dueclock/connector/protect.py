"""Protect the API key at rest. On Windows the key is encrypted with DPAPI (readable only by the same Windows user on the same PC).

Elsewhere (development only) a clearly marked, unencrypted encoding is used so tests and non-Windows installs still work.
The blob is base64 with a prefix saying how it was protected.
"""
from __future__ import annotations

import base64
import sys
from typing import Protocol


class Protector(Protocol):
    def protect(self, secret: str) -> str: ...
    def unprotect(self, blob: str) -> str: ...


class PlainProtector:
    """NOT encryption. Development and non-Windows only."""
    PREFIX = "plain:"

    def protect(self, secret: str) -> str:
        return self.PREFIX + base64.b64encode(secret.encode()).decode()

    def unprotect(self, blob: str) -> str:
        if not blob.startswith(self.PREFIX):
            raise ValueError("not a plain-protected value")
        return base64.b64decode(blob[len(self.PREFIX):]).decode()


class DpapiProtector:
    PREFIX = "dpapi:"

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ct, self._wt = ctypes, wintypes

        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        self._BLOB = BLOB

    def _call(self, fn_name: str, data: bytes) -> bytes:
        ct = self._ct
        buf = ct.create_string_buffer(data, len(data))
        src = self._BLOB(len(data), ct.cast(buf, ct.POINTER(ct.c_char)))
        out = self._BLOB()
        fn = getattr(ct.windll.crypt32, fn_name)
        args = (ct.byref(src), None, None, None, None, 0, ct.byref(out)) if fn_name == "CryptProtectData" else (ct.byref(src), None, None, None, None, 0, ct.byref(out))
        if not fn(*args):
            raise OSError(f"{fn_name} failed")
        try:
            return ct.string_at(out.pbData, out.cbData)
        finally:
            ct.windll.kernel32.LocalFree(out.pbData)

    def protect(self, secret: str) -> str:
        return self.PREFIX + base64.b64encode(self._call("CryptProtectData", secret.encode())).decode()

    def unprotect(self, blob: str) -> str:
        if not blob.startswith(self.PREFIX):
            raise ValueError("not a DPAPI value")
        return self._call("CryptUnprotectData", base64.b64decode(blob[len(self.PREFIX):])).decode()


def get_protector(platform: str | None = None) -> Protector:
    return DpapiProtector() if (platform or sys.platform).startswith("win") else PlainProtector()


def unprotect_any(blob: str, protector: Protector | None = None) -> str:
    """Read a stored key whichever way it was protected."""
    if blob.startswith(PlainProtector.PREFIX):
        return PlainProtector().unprotect(blob)
    if blob.startswith(DpapiProtector.PREFIX):
        return (protector or DpapiProtector()).unprotect(blob)
    raise ValueError("unknown key protection format")
