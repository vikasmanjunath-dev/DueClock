"""DueClock Tally connector: reads TallyPrime read-only and pushes to the cloud core.

Never writes to Tally. Survives restarts (durable local queue), retries with backoff, sends only changes,
and refuses to report a mass settlement it cannot explain.
"""
CONNECTOR_VERSION = "0.1.0"
