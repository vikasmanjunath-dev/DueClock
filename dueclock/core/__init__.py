"""DueClock core: the tested, persistence-backed engine behind the product.

Modules
- rates:        dated RBI bank-rate table
- clock:        pure statutory-clock engine (due dates, interest, coverage, year-end exposure)
- store:        per-customer databases, API-key control plane, tenant-scoped repository
- ingest:       idempotent connector sync
- tally_parse:  Tally XML / export parser
- cadence_plan: reminder planner (which step, when, and why not)
- api:          FastAPI router (/v1)
"""
