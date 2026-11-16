"""Vercel entry point: one Python function serving the whole FastAPI app. Vercel routes every path here (vercel.json).
The filesystem is read-only except /tmp, so the data directory (used only for a token-secret file when DUECLOCK_TOKEN_SECRET is unset) points there;
in production DUECLOCK_ENV=production demands DUECLOCK_TOKEN_SECRET anyway, so nothing is ever written."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DUECLOCK_DATA_DIR", "/tmp/dueclock")
os.environ.setdefault("DUECLOCK_TRUST_PROXY", "1")          # Vercel terminates TLS and forwards the client address

from dueclock.core.preflight import enforce_in_production   # noqa: E402
from dueclock.core.api import create_core_app               # noqa: E402

enforce_in_production(os.environ)
app = create_core_app()
