# Task — BFF: surface per-option bid/ask + volume in the analytics payload

**Status:** open — **P1** (2026-06-17). **Lane:** `frontend-` (BFF only — backend, parallel-safe).
**Prerequisite for:** the Onglet-1 tenor price-structure block (`frontend-3onglets-target-ux.md` §Onglet 1 ③).
**Grounding:** Greeks transcript `:14` ("données de marché **bid/ask + volume**") — the trader reads the
**spread/liquidity**, not a mid average.

## Why

The Onglet-1 tenor panel must show, per strike, **bid / ask / volume** (not a mid). The data exists at
the snapshot layer (`infra/contracts/tables.py:44-45` — `bid`/`ask`; volume landed via the
option-volume-capture lane), but it is **not threaded into the analytics payload** the front reads
(`/api/analytics` currently serves IV/greeks/mid per cell).

## Scope

- Thread **bid / ask / volume** per option onto the analytics grid cell the BFF serializes
  (`apps/frontend/src/algotrading/frontend/routers/analytics.py` + `serializers.py`), read from the
  banked snapshot/quote layer for the as-of/run.
- **Additive + byte-identical-when-absent**: a cell with no banked bid/ask renders exactly as today
  (nullable fields; the front shows "n/a"/the spread as a gap). No recompute in the BFF.
- Tests: serializer carries bid/ask/volume when present, omits cleanly when absent; a fixture with a
  two-sided quote and a fixture with a one-sided/empty quote.

## Acceptance

- `/api/analytics` cells carry `bid`, `ask`, `volume` (nullable) per option/strike.
- Existing analytics tests stay green (byte-identical-when-absent); new bid/ask tests pass.
- Python gate green (ruff/mypy/import-linter/pytest). Off the page-1 web lane — BFF + tests only.
