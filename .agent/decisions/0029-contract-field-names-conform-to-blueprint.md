# 0029 — Contract field names conform to the blueprint data dictionary

- **Status:** accepted, 2026-06-06. Resolves **OQ-7** (`.agent/open-questions.md`).
- **Date:** 2026-06-06
- **Implements:** blueprint **Part IX** (data dictionary) under the blueprint-is-authority
  rule ([ADR 0011](0011-blueprint-is-plan-of-record.md)). Adds no rule the blueprint does
  not already state — it makes the code match the names the blueprint already publishes.
- **Relates to:** [[0011-blueprint-is-plan-of-record]] (the authority this enforces),
  the frozen `contracts` seam (the tables this renames).

## Context

The H2 doc reconciliation (2026-06-06) found the blueprint data dictionary
(`documentation/blueprint/09-data-dictionary.md`, Part IX) and the code's frozen table
contracts (`infra/contracts/tables.py`) spelled the same persisted fields differently —
the dictionary used the full economic name, the code an abbreviation:

| Blueprint (Part IX) | Code (was) | Table |
|---|---|---|
| `forward_price` | `forward` | `ForwardCurvePoint` |
| `implied_vol` | `iv` | `IvPoint` |
| `log_moneyness` | `k` | `IvPoint` |
| `scenario_pnl` | `pnl` | `ScenarioResult` |
| `qc_status` | `status` | `QcResult` |
| `dollar_gamma` (+ delta/vega) | `cash_gamma` (+ `cash_delta`/`cash_vega`) | `PricingResult` |

These are spelling/abbreviation differences, not domain disagreements: `delta`, `gamma`,
`vega`, `theta`, `total_variance`, `maturity_years`, etc. already matched. Per ADR 0011 the
blueprint is authoritative on the data contract, so the divergence was raised as OQ-7 rather
than silently doc-edited, and put to the owner.

## Decision

**The code conforms to the blueprint.** The frozen contract fields are renamed to the
blueprint data-dictionary names (the table above). The owner ruled "follow the blueprint,
and start the data from scratch" — so the schema change is free: the persisted Parquet
column names derive from the dataclass field names (`storage/schema.py`), and there is no
canonical stored dataset to migrate. The "frozen" contracts take this one deliberate,
recorded conformance rename; frozen means *immutable across a release*, not *never corrected
to its own source of truth*.

Scope applied: the six persisted-column renames above, their `infra/contracts/registry.py`
validation literals, every producer/consumer (mypy on the slotted frozen dataclasses
enumerated them), the frontend scenario serializer key (`pnl` → `scenario_pnl`), tests, and
the module READMEs/docstrings that named the old fields.

**Deliberately not renamed:** `InstrumentKey.broker_contract_id` (the dictionary's
`contract_id_broker`). It is not a standalone persisted column — it is a value embedded in
the canonical instrument-key string — and renaming the attribute threads through the
replay-critical canonical-key machinery for nominal gain only. Flagged for a follow-up if
full nominal conformance is wanted; not blocking.

## Consequences

- The data dictionary now describes the code exactly; a reader can map every Part IX field
  to a real contract column. No doc edit to the dictionary was needed — the code moved to it.
- Content hashes and Parquet schemas for these tables change (field names feed both). This is
  acceptable and intended given the "data from scratch" ruling; no migration is performed.
- The whole root gate is green after the rename (ruff, mypy 176 files, import-linter, pytest).
- Future field-name choices follow the blueprint by default; a new abbreviation that diverges
  is a bug, not a style choice.
