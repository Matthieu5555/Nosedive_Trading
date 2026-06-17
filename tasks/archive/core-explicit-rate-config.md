# T-explicit-rate-parameter — make the interest rate an explicit, displayed, modifiable input

**Status:** specced; **step 1 LANDED** (verified 2026-06-17). **The blueprint settles the design**
(no owner decision needed) — see citations. Folds with the queued **T-scenario-rate-axis** (rate
*shocks* are the scenario form of the same parameter).

> **STATUS UPDATE (2026-06-17 board audit):** step 1 is real — `ForwardConfig.rate: float | None`
> (`platform_config.py:362`), shipped `rate: null` (`configs/pricing.yaml:51`), consumed by
> `_carry_and_dividend` (`estimate.py:111-123`). **Still open:** the persisted `forward_curve` contract
> (`ForwardCurvePoint`/`ForwardDiagnostics`) drops `implied_rate`/`implied_carry`/`implied_dividend` at
> serialization (`bundles.py:6-12`), so the rate/carry/dividend diagnostic is not surfaced to BFF/front;
> and the `r(T)` curve form + value-changing display default remain unbuilt.

## The ask (owner, 2026-06-13)

> « on doit avoir TOUT nos greeks … y compris ceux liés aux taux d'intérêt, car on sous-entend
> un IR fixe mais ça doit rester un paramètre modifiable et donc être explicitement affiché. »

## What the blueprint says (checked first, per the absolute rule)

The rate is **not** meant to be an implicit back-derived constant — the blueprint treats
`r(T)` as an **explicit input** that drives the forward and the implied-carry diagnostic:

- **02-math-framework, Eq 2** (parity forward): `F(T) ≈ K + e^{rT}(C(K,T) − P(K,T))` — the rate
  enters the forward.
- **Eq 5** (implied carry/dividend): `q(T) = r(T) − (1/T)·ln(F(T)/S₀)` — `r(T)` is the **input**,
  `q` is **derived**. You cannot split the forward's carry into rate vs dividend without `r(T)`.
- **04-implementation-guides** pseudocode: `estimate_forward(snapshot, maturity, rate, config)`
  → `f_i = strike + exp(rate·T)·(call_mid − put_mid)`. The rate is literally a parameter.
- **Step 6(f)** (roadmap): *"If a rate curve is available, derive implied carry/dividend yield
  and compare it with expectations."* — a **rate curve** `r(T)` is a first-class, optional input.
- **forward_curve** table = *"Forward and implied carry diagnostics"* — the implied carry
  (computed with `r`) is a **persisted diagnostic** meant to be surfaced.

So: explicit rate parameter (a flat rate, or a curve `r(T)`), **displayed** with the implied
carry/dividend, and **modifiable**. The blueprint pins the *design*; it does not pin the
modify-UX (config edit vs live reprice) — that is an implementation choice, below.

## Current state (measured)

The rate is **implicit**: `pricing/black76.py:_implied_rate(DF, T) = −ln(DF)/T` back-derives it
from the discount factor (itself from the PCP forward). Rho is computed against this implied
rate. There is **no typed-config home** for the rate → an ADR-0028 gap (an economic input
living as a derived `.py` quantity, the same class as `T-delta-step-2`).

## What to build

1. **Typed-config rate** (ADR 0028): a `rate` (flat, MVP — placeholder like other configs) or a
   `rate_curve` `r(T)` in `pricing.yaml`, hashed into `config_hashes["pricing"]`. Flat is an
   acceptable MVP per Step 6(f) ("if a rate curve is available"); document it as a flat-curve
   placeholder.
2. **Wire it through** the forward engine (Eq 2 uses `rate`) and the implied carry/dividend
   (Eq 5 → persist on `forward_curve`). Keep the PCP forward observable; use `r` to *split*
   carry into rate vs dividend (the blueprint's intent), and as the rho basis.
3. **Display** the rate + implied carry/dividend per tenor with the rest of the greeks (the
   `forward_curve` diagnostic is already a persisted table — surface it at the BFF + front).
4. **Modifiable** — two coherent options, pick per effort (NOT a blueprint fork):
   - **(a) config edit + re-run** analytics (cheapest; the rate is a hashed config input, so a
     change re-stamps the run — clean reproducibility).
   - **(b) live override that re-prices on the page** (richer; precedent exists — the basket
     stress surface already reprices on-demand in the BFF, `routers/basket.py`). This is also
     where **T-scenario-rate-axis** (rate shocks) plugs in: a base rate + a shock axis.

## Acceptance (sketch)

Independent oracle: with a flat `r`, `F = K + e^{rT}(C−P)` and `q(T) = r − ln(F/S)/T` recover
hand values; rho moves with `r` as `∂price/∂r`; the displayed rate == the config rate (not the
back-derived one once the input exists). Look-ahead clean; golden + gate green.

## Touches

`configs/pricing.yaml`, `core/config` (rate field), `infra/forward` + `infra/pricing` (rate as
input, implied carry), `forward_curve` contract/serializer, BFF + front display. Disjoint from
`T-delta-step-2`.

## Landed — step 1: the typed-config rate home (the ADR-0028 gap), zero-churn by default
`ForwardConfig.rate` (`float | None`, `pricing.yaml` under `forward:`) is the explicit
interest-rate **input** the blueprint pins (Eq 5). `forwards/estimate.py._carry_and_dividend`
now takes it: when set it is used as `r` for the split `q = r − ln(F/S)/T` and returned as the
rate; when `null` (the default, and what the yaml ships) it falls back to the parity-DF-implied
`r = −ln(DF)/T` — **byte-identical** to before, so no analytics/forward golden moved (only the
`pricing` config-hash, by design). Tests: explicit override reproduces Eq 5 by hand; the `None`
default keeps the parity-implied rate. Gate green (1288).

## Landed — step 2: surface rate/carry/dividend on the `forward_curve` contract + per-tenor BFF
`ForwardCurvePoint` gained `implied_rate`/`implied_carry`/`implied_dividend` (`float | None = None`,
`tables.py`), threaded from the `ForwardEstimate` in `forward_curve_point` (`estimate.py`). The
storage golden gained the three columns additively (null where unset); the determinism golden
(`forward_stamp_hash` + `forward_price`) stayed byte-identical (provenance is content-addressed off
source records + config_hashes, not contract fields). BFF: `/api/analytics` now reads `forward_curve`
per maturity and joins a `rate_diagnostics` object onto each tenor entry —
`{forward_price, implied_rate, implied_carry, implied_dividend, rate_unit}`, `rate_unit =
"/yr (annualized, continuous)"`, the whole object `null` when no forward point exists for that
maturity. **No recompute in the BFF** — it surfaces whatever the persisted point holds (with
`rate: null` that is the parity-implied `r`). Tests: `forward_curve_point` carries both the
parity-implied (`rate: null`) and the explicit-`r` Eq-5 split; the BFF payload carries them per
tenor and is null when unseeded. Gate green (2455 passed, 12 skipped).

**Open (continuation):** **front display** — A6 adds the `api.ts` type + renders `rate_diagnostics`
in the Onglet-1 ③ Panneau Ténor (payload shape above); the `r(T)` curve form; and the
value-changing MVP default (`rate: 0.0`) once the owner wants the carry split to use a flat 0
instead of the market rate.

## Tech-lead assessment (Surface & Analytics family, 2026-06-17) — no actionable backend work left
Measured the three open items against the references; each resolves to gated or out-of-scope:
- **`r(T)` curve form — OWNER-GATED.** This is `infra-rates-curve-ingest` (R1), which the board marks
  "needs an ADR + blueprint amendment first; not pickable yet," and whose design lives in **ADR 0054
  (status: Proposed 2026-06-17, not yet ruled)**. The blueprint's own `r(T)` text
  (`02-math-framework.md:49-51`) is a "proposed amendment — pending owner acceptance of ADR 0054 …
  not yet ratified canon." The pillar set, interpolation convention, and per-currency source are
  explicitly listed as owner rulings. Building it now would pre-empt an unratified ADR. **Not built.**
- **Value-changing display default (`rate: null → 0.0`) — OWNER ECONOMIC RULING, not engineering.**
  The spec itself gates it on "once the owner wants the carry split to use a flat 0." Flipping a
  hashed economic input that moves the implied carry/dividend diagnostic for every run is an owner
  decision, not a free flip. **Not changed.**
- **Front display (A6) — OUT OF SCOPE.** Frontend is owner-owned (`apps/frontend/**`).

The two landed steps (typed `ForwardConfig.rate` home + `forward_curve` `rate_diagnostics` BFF join)
stand. Verdict: **nothing for an implementer to land in this family right now** — the remainder is
owner-gated (ADR 0054) or frontend.
