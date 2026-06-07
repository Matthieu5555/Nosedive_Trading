# 0036 — $-Greek units + monetization conventions (raw is truth, dollar is derived)

- **Status:** accepted, 2026-06-07 (formalises the owner ruling of 2026-06-05).
- **Date:** 2026-06-07.
- **Amends:** the **blueprint** — the dollar conventions live in Part IX (data dictionary)
  and the Part V math notes. This ADR is the OQ-1 formalisation that the blueprint
  transcription left as a `(blueprint)` follow-up; the data-dictionary rows
  (`dollar_delta`/`dollar_gamma`/`dollar_vega`/`dollar_theta`/`dollar_rho`) are added in the
  same change.
- **Relates to:** [[0011-blueprint-as-plan-of-record]] (the data dictionary overrides on any
  formula/field conflict), [[0028-configuration-and-reproducibility-standard]] (the two
  convention forks become hashed config flags), [[0029-contract-field-names-conform-to-blueprint]]
  (the `dollar_*` naming the new fields follow). Resolves **OQ-1** in
  [`open-questions.md`](../open-questions.md).

## Context

`infra/risk` and `infra/pricing` already compute monetized Greeks beside the raw ones, but
the *convention each dollar figure is quoted in* was never pinned, so the BFF could only
carry a bare number with no unit. Two genuine forks were buried as silent assumptions:
**gamma** can be quoted per 1% move or per \$1 move, and **theta** can use a 365- or
252-day divisor. Until these are fixed and carried with units, the front cannot label a
dollar number correctly and the basket builder (Phase 2) cannot sum positions safely.

## Decision

**Raw per-unit Greeks are the source of truth; the dollar layer is a derived view, each
number quoted in an explicit unit.** The five unit definitions, verbatim:

- **Delta\$** `= Δ·S·mult` — per **\$1** of underlying.
- **Gamma\$** `= Γ·S²/100` — per **1% move** (this is the 1%-vs-\$1 fork; see the flag).
- **Vega\$** `= vega·0.01·mult` — per **1 vol point** (0.01).
- **Theta\$** `= theta·mult/365` — per **calendar day** (this is the 365-vs-252 fork).
- **Rho\$** `= rho·0.01·mult` — per **1% rate**.

**Additivity:** per-contract (×mult) → per-position (×qty) → a book is the additive sum of
per-position dollar numbers. The dollar layer is kept per-position so the Phase-2 basket
builder sums positions without reworking the metric contract.

**The two genuine forks are explicit, hashed config flags** (not buried constants), in the
`scenarios` bundle (the risk-layer params, ADR 0028) — so they enter
`config_hashes["scenarios"]`:

- `gamma_normalisation` ∈ {`one_pct` (default, Γ·S²/100), `one_dollar` (Γ·S²)}.
- `theta_day_count` ∈ {`365` (default, per calendar day), `252` (per trading day)}.

The defaults match the pinned units (gamma per 1%, theta ÷365).

**Contract + boundary:**

- `PricingResult` carries `dollar_theta` and `dollar_rho` beside the existing
  `dollar_delta`/`dollar_gamma`/`dollar_vega`. The two new fields are **additive-nullable**
  (`float | None`) so a partition written before they existed still reads (the
  schema-evolution discipline of ADR 0029 / the storage codec).
- At the **BFF boundary** each dollar number is emitted as `{raw, dollar, unit}` — the raw
  per-unit Greek, the dollar value, and the explicit unit string (e.g. gamma → "$ per 1%
  move", theta → "$ per calendar day") — never a bare float. There is no `/api/market`
  router (deleted by C4); the metric contract is the post-C4 readback path pinned by
  `apps/frontend/tests/test_readback_api.py`.

The canonical conversions and the two forks live once, in
`packages/infra/src/algotrading/infra/pricing/dollar_greeks.py`.

## Consequences

- The dollar layer is complete (five Greeks), self-describing (unit string per number),
  and reproducible (the two forks are hashed inputs, so a stored result traces to the exact
  convention that produced it).
- A change to either flag moves exactly the `scenarios` bundle hash; the defaults preserve
  today's numbers for delta/vega/rho and pin gamma to per-1% / theta to per-calendar-day.
- The existing engine adapter (`pricing.pricing_result`) keeps `dollar_gamma = Γ·S²` for
  backward compatibility with the frozen pricing-interface tests; the flag-driven
  `dollar_greeks` module is the single home of the configurable conversions that the
  projection/risk layers consume going forward (1F / Phase 2).
