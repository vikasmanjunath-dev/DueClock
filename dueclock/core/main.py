"""ASGI entry point: ``uvicorn dueclock.core.main:app`` (data directory from DUECLOCK_DATA_DIR)."""
import os

from .api import create_core_app
from .preflight import enforce_in_production
from .scheduler import attach

enforce_in_production(os.environ)          # DUECLOCK_ENV=production: refuse to start on an unsafe configuration
app = create_core_app()
attach(app, os.environ)                    # DUECLOCK_SCHEDULER=1: run reminders inside this process every DUECLOCK_SCHEDULER_MINUTES
