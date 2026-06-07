# risk — portfolio Greeks, monetized sensitivities, aggregation, scenarios, reconciliation

TL;DR: pure functions that take positions plus their resolved market state, price each
line through M2's frozen pricer, net the lines into portfolio sensitivities grouped by
any configured key, reconcile against the broker, and stress the book under explicit
shocked market states. This is the risk engine (roadmap steps 11–12) — the payload the
whole backbone exists to produce: trustworthy risk and worst-case stress PnL. For the
non-obvious design choices behind it (the net convention, rho, the scenario-grid rules)
read ADR 0006, which this points to rather than restates.

```python
from algotrading.infra.risk import (
    ContractValuationInput, position_risk, aggregate_lines,
    scenario_grid, scenario_line_pnls, worst_case, build_scenario_report,
    build_risk_snapshot, reconcile,
)

line  = position_risk(portfolio_id="pf-1", quantity=10.0, valuation=valuation)
net   = aggregate_lines([line], portfolio_id="pf-1", dimension="underlying")
cells = scenario_line_pnls([line], scenario_grid(scenario_config))
wc    = worst_case(cells)   # most negative portfolio PnL + ranked contributors
```

## Status: landed and in the gate

This package is complete and green in the root gate. It binds the pricing seam
(`algotrading.infra.pricing`: `PricingState`, `from_spot`, `price`, `PriceGreeks`) and
the M0-frozen contracts (`algotrading.infra.contracts`: `RiskAggregate`,
`ScenarioResult`). The one place that binds the pricing seam is `valuation.py`; if the
pricer ever changes names, that adapter is the single point to reconcile, because the
rest of the package depends on `ContractValuationInput`, not on the pricer directly.

## How this package was merged (blueprint-driven bake-off)

Two independent risk engines were merged. The blueprint is the absolute reference, so
each concern was decided by what the blueprint mandates, not by which side was written
first:

- **Persisted contracts, Greeks/bumps/valuation, scenario engine, reconciliation core —
  ours.** The frozen `RiskAggregate (… group_key, net_*)` and `ScenarioResult (…
  scenario_id, contract_key, … scenario_pnl)` shapes are exactly the blueprint's data dictionary
  rows (`04-implementation-guides.md`), and our emission adapters project straight into
  them. The shared versioned bump source answers the blueprint's explicit anti-drift
  requirement (`05-math-notes.md` §4); `effective_scenario_version` makes the grid
  version tamper-evident (§5); the non-finite-broker-Greek guard keeps reconciliation a
  real diagnostic.
- **Versioned risk snapshot, scenario report attribution, positions / basket / config —
  adopted from Vincent's build.** The blueprint's `risk/aggregation.py` says "version the
  risk snapshot with analytics version and position source timestamp" (→ `snapshot.py`),
  and `risk/scenarios.py` says "allow scenario families to be filtered by report type"
  and "include top-contributor extraction in the core API" (→ `ScenarioReport`,
  `FamilyAttribution`, `UnderlyingAttribution`). The positions book model, the basket
  variance identity (Eq. 23), and config-driven grouping/thresholds are additive surface
  the blueprint mandates.

The M3 task's own bake-off recommended the same split; this re-derivation from the
blueprint is what settles it, with one decisive piece of new evidence — the frozen
contracts match our projection, not Vincent's in-module shapes.

## The boundary: risk never prices

The single most important thing to understand is what risk does *not* do. It never
implements an option-pricing formula. It builds the pricer's `PricingState` and calls
M2's frozen `price()` for every value and every Greek — analytic for European, the
binomial lattice for American. M2 owns pricing; risk owns *position* math (scaling by
multiplier and quantity), aggregation, reconciliation, and the scenario grid.

That boundary is enforced by construction. The input is one resolved
`ContractValuationInput` per contract, not M2's analytics contracts; risk builds the
pricing state through `pricing.from_spot`, so the pricer's invariant
`forward == spot * exp(carry * T)` holds automatically and risk can never hand the pricer
an inconsistent state. The analytics-contract join lives in exactly one thin assembly
step (M7's wiring), not in this math.

## Data flow

```text
ContractValuationInput  (one per contract: spot, carry, vol, df, strike,
        |                right, style, multiplier, currency, confidence)
        |  pricing_state_for -> pricing.from_spot
        v
  M2's frozen price()  ->  PriceGreeks (per-unit price, delta, gamma, vega, theta, rho)
        |
        v
   PositionRisk  (the line: inputs carried verbatim + per-unit Greeks;
        |         position_* and dollar_* are derived properties)
        |
        +--> net_lots --> aggregate_by_key (config-driven) --> NetSensitivities
        |                        |  risk_aggregate
        |                        v
        |                   contracts.RiskAggregate   (persisted, stamped)
        |
        +--> reconcile(broker) -> GreekDiscrepancy / ReconciliationReport (breaches only)
        |
        +--> scenario_grid(config) -> scenario_line_pnls -> worst_case / build_scenario_report
                                           |  scenario_result
                                           v
                                  contracts.ScenarioResult   (persisted, stamped)

   build_risk_snapshot(positions, valuations, params) bundles the lines, the configured
   aggregates, an optional reconciliation report, and the provenance (analytics version,
   position source + timestamp, config version, code version) into one RiskSnapshot.
```

The emission adapters (`risk_aggregate`, `scenario_result`) take an injected provenance
stamp and read no clock, so a risk row reproduces byte-for-byte in replay.

## The line and its sensitivities (step 11)

`position_risk` returns a `PositionRisk`: the `ContractValuationInput` carried verbatim
plus the per-unit `PriceGreeks` from the pricer. Everything monetized is a derived
property, so it cannot drift from the per-unit Greeks it scales. The scale factor is
`multiplier * quantity` (signed).

Position-level (`position_delta`/`gamma`/`vega`/`theta`) are `per_unit * M * Q` —
share/contract-equivalent. These are what aggregate, because a per-unit sum across
contracts with different multipliers would be meaningless. Dollar-monetized figures are
currency-tagged cash and live only on the line; they are *not* summed into the aggregate
(adding USD and EUR dollar gamma is a category error):

| Property       | Formula                  | Per what move                      |
| -------------- | ------------------------ | ---------------------------------- |
| `dollar_delta` | `delta * spot * M * Q`   | cash PnL per 1.00 spot move        |
| `dollar_gamma` | `gamma * spot² * M * Q`  | dollar gamma (Eq 17)               |
| `dollar_vega`  | `vega * 0.01 * M * Q`    | cash PnL per one vol point (Eq 18) |
| `dollar_theta` | `theta * M * Q`          | cash PnL per year of decay         |

`central_difference_greeks` is the independent cross-check, not the production path: it
central-differences the *pricer's own price* using the shared bumps. The test that
analytic and central-difference Greeks agree catches a sign or unit error. `rho` is
filled from the forward-fixed identity `-T * price`, not differenced (ADR 0006 point 4).

## Aggregation and the risk snapshot

`aggregate_lines` groups lines by an intrinsic `dimension` (`instrument`, `maturity`,
`underlying`) and nets each group into `NetSensitivities`, keeping its lines so the
result stays explainable. `aggregate_by_desk` groups by a caller-supplied
`contract_key -> desk` map; an unmapped contract falls into `desk:unassigned` rather than
being dropped. `aggregate_by_key` is the config-driven dispatcher the snapshot uses, and
`resolve_grouping_key` validates a configured key name at load time. `risk_aggregate`
projects one group onto the frozen `RiskAggregate`.

Two invariants hold by construction: the sum of the lines equals the aggregate, and the
aggregate is a pure function of the input *set*, not of arrival order. Order-independence
comes from netting same-contract lots first (`net_lots`) and sorting by contract key. A
net-flat contract is kept as a zero-quantity line, not dropped. This is exactly what
byte-identical replay depends on; ADR 0006 point 7 explains why netting was chosen.

`build_risk_snapshot` is the canonical entry point: it joins a `PositionSet` to its
resolved valuations, prices each line, aggregates by every configured grouping key,
optionally reconciles against broker Greeks, and stamps the result with the analytics
version, the position source and its timestamp, the config version, and the code version
— so a stored `RiskSnapshot` regenerates from a named, dated book (the blueprint's
snapshot-versioning requirement). A position with no valuation raises
`MissingValuationError`, never a silent drop.

## Reconciliation

`reconcile(line, broker)` compares per-unit Greeks against broker-returned ones and
returns only the breaches — the Greeks whose absolute difference exceeds a versioned
per-unit threshold (`DEFAULT_RECON_TOLERANCE`: delta/gamma 1e-3, vega/theta 1e-2). An
absent broker Greek (`None`) is skipped (not a disagreement); a non-finite broker value
(NaN/inf) is surfaced as a breach — corrupt data is not agreement, and without the
explicit check `nan > threshold` is `False`, so a NaN would otherwise read as "agrees".
`reconcile_report` runs the comparison over a whole book, returns a `ReconciliationReport`
(the breaches plus the compared-pair count, so a breach rate has its denominator), and
logs a warning when breaches surface. Broker Greeks are a diagnostic, never the source of
truth.

## Scenario stress (step 12)

A scenario is an explicit shocked market *state*, never a Greek multiplier.
`scenario_grid(config)` builds a deterministic grid from `ScenarioConfig` (`spot_shocks`,
`vol_shocks`) plus two construction rules: a combined crash (most adverse spot move paired
with the largest vol spike) and a small time roll-down (`ScenarioConfig.roll_down_days`, Actual/365).
`scenario_line_pnls` produces every `(scenario, line)` cell (the cartesian product, so
completeness is structural); `worst_case` returns the scenario with the largest portfolio
loss plus its lines ranked worst-first. `scenario_result` projects a full-reprice cell
onto `ScenarioResult`.

`build_scenario_report` is the reporting surface: per-scenario totals, the worst case with
its ranked contributors, per-underlying attribution of the worst case, and per-family
worst cases — everything derived from the same full-reprice cells, so the report is a pure
function of (lines, grid) and reproduces from snapshot + positions + scenario version.

Shock conventions, asserted by tests:

- `spot_shock` is relative: `new_spot = spot * (1 + spot_shock)`.
- `vol_shock` is additive in vol units, floored at zero.
- `time_shock` is a roll-down in years; the discount factor rolls to the shortened
  maturity at the implied rate. Carry is held fixed, so the forward tracks the shocked
  spot.

`full_reprice_pnl` is always the source of truth and the only thing persisted.
`local_approx_pnl` is a fast Taylor (Eq 19) path for intraday checks, tested to agree with
full reprice for small shocks and to *diverge* for a large adverse shock.
`local_approx_pnl_fd` is the same expansion with finite-difference Greeks from the shared
bump source. The approximation never lands in storage.

## By-Greek PnL attribution (2C)

`attribution.py` is the *across-Greeks* axis: it splits the local Taylor PnL into its named
dollar contributions — `delta_pnl = Δ·dS·scale`, `gamma_pnl = ½·Γ·dS²·scale`,
`vega_pnl = Vega·dσ·scale`, `theta_pnl = Θ·dt·scale` (blueprint Eq 19) — and reports the
**residual** of their sum against the **full reprice** (the ADR-0006 oracle). The full
reprice is the truth, the split is the explanation, and the residual is the honest accuracy
of that explanation: bounded-and-reported within tolerance for a small shock, material-and-
labeled for a large one, never silently dropped. A non-finite contribution or reprice is a
labeled diagnostic (mirroring `reconciliation.py`'s NaN guard), not silent agreement.

The term arithmetic has **one home**: `taylor_terms` in `scenarios.py`, which `_taylor_pnl`
(the lumped path) now delegates to — so the split can never drift from the lump (the
`test_terms_sum_to_lumped_taylor` refactor-equivalence invariant). `attribute_line` builds
the per-position record, `attribute_book` the term-wise sum over the netted lines (via
`math.fsum`, so it is invariant under input reordering — the D-owned invariant). This axis
is **orthogonal** to the across-positions `UnderlyingAttribution`/`FamilyAttribution` (a book
is sliced both ways independently); do not conflate them.

Contributions are dollar PnL and book-additive (ADR-0029 `dollar_*`/`*_pnl` names, never
`cash_*`). Two convention flags ride on `AttributionConfig` (the new attribution section of
`RiskParams`, C7-DI): `gamma_normalisation` (`one_dollar` default = Eq-19 ½Γ(dS)²; `one_pct`
÷100) and `theta_day_count` (365 default = calendar, matching the grid; 252 = trading,
×365/252). Both are *reporting normalisations on the decomposition only* — they move that one
term and the residual, never the full reprice. They flow from validated config into the pure
builder and enter the stamp `config_hashes`; defaults reproduce the blueprint Eq-19 lump.
`line_attribution_result`/`book_attribution_result` project into the frozen
`ScenarioAttribution` contract the BFF/1I read (1I renders the Δ→Γ→Vega→Θ→residual→full
waterfall). A book record carries the `__book__` sentinel in `contract_key` so it never
collides with a per-line record. See ADR 0038.

## One bump source, determinism, and versioning

Every finite-difference perturbation lives in one versioned `BumpSpec`, `DEFAULT_BUMPS`
(`bumps.py`), imported by both the Greeks cross-check and the scenario FD path, so they
cannot silently diverge — the classic hidden risk/scenario disagreement the blueprint
calls out. The scenario grid is versioned by `effective_scenario_version(config)` — the
config section version folded with a hash of the grid-construction constants — *not*
`config.version` alone, which would let two different grids share one version. That
effective version is persisted on every `ScenarioResult`, so worst-case loss regenerates
exactly. The grid also de-dupes configured shocks and refuses a residual id collision, so
a repeated shock can never collapse a cell or double-count the worst case. ADR 0006 has
the full rationale.

## Positions and basket

`positions.py` is the working book model: a `Position` is a signed `Decimal` quantity of a
contract plus desk `tags`; a `PositionSet` bundles the latest positions with the source
identity and timestamp that version any snapshot built from them. `hypothetical_positions`
wraps a hand-built book for paper mode — the seam a live broker-positions source mirrors.
The persisted/seam shape is `contracts.Position`; this is the in-memory model.

`basket.py` is the generic basket / index variance identity (Eq. 23): given constituent
weights and vols plus either a full pairwise correlation matrix or a single
average-correlation assumption, it returns the implied basket variance and a
diversification diagnostic. A reusable risk primitive, not strategy logic.

`multileg.py` is a **different thing** — do not conflate it with `basket.py`. It prices and
risks a **multi-leg position basket** (WS 2A): given a `contracts.Basket` (its `BasketLeg`s),
the matching `ProjectedOptionAnalytics` rows, and the spot for stock legs, `basket_risk` returns
a `BasketRisk` whose dollar Greeks are the **book-additive sum** over legs of
`signed_quantity · row.dollar_<greek>` (option legs) plus the linear spot delta (stock legs). It
is **summation, never a recompute** — it reads the dollar Greeks WS-1F already produced and sums
them, in the analytics **per-1% / per-365** convention carried on the rows; it never imports the
legacy per-`$1` `PositionRisk` dollar Greeks (mixing the two normalisations is silently wrong by
100×). A leg that resolves to no row (or an ambiguous provider, or a missing spot) is a labelled
`BasketGap`, never a silent zero; an additive-nullable theta/rho missing on a contributing leg
makes that basket Greek `None` + a gap, never a partial sum. The per-leg `LegRisk` contributions
are preserved beside the aggregate (the proof the total is the sum, and what 2C attributes off).

## Failure modes

The core is pure, so failures are input-validation and corrupt-join errors, not transient
I/O. The caller fixes the input.

| Raised by | When | Meaning |
| --------- | ---- | ------- |
| `ValuationError` | non-positive/non-finite `multiplier`, empty `currency`, or bad `confidence` | malformed valuation input; carries field/value/reason |
| `LotConsistencyError` | two lots of one `(portfolio, contract)` disagree on market state | a corrupt join; netting would silently pick one |
| `AggregationError` | unknown grouping dimension, or `desk` without `desk_of` | caller programming error |
| `ScenarioGridError` | colliding scenario ids after de-dup | config shocks format to the same id; surfaced loudly |
| `MissingValuationError` | a position has no resolved valuation | a gap in the risk picture, never silently dropped |
| `ValueError` | `worst_case`/empty grid, malformed `basket_variance` inputs | no answer over nothing |

A low-confidence contract is *not* a failure: the `"low"` quote-QC label rides through on
the line so it can be surfaced, and the position is still priced.

## Fastest way to exercise it

```bash
uv run pytest packages/infra/tests/test_risk.py packages/infra/tests/test_scenario.py -q
uv run pytest packages/infra/tests --cov            # with the branch-coverage floor
```

`test_risk.py` covers step 11 (Greeks vs. an independent oracle, the
analytic-vs-central-difference cross-check, monetization, aggregation, reconciliation,
edges); `test_scenario.py` covers step 12 (full reprice vs. oracle, worst case,
small-agree / large-diverge, completeness, report attribution); `test_seam_risk.py` pins
the risk→contracts round-trip and the risk→pricer interface; `test_risk_properties.py` is
the reordering-invariance and sum-of-lines property suite; `test_determinism_risk.py` is
the golden and cross-process-hash determinism suite. Independent oracles are a hand-coded
GBSM model cross-checked against QuantLib / py_vollib.

## See also

- `../pricing/README.md` — M2's frozen pricer this builds on, and the cash-Greek conventions.
- ADR 0006 — the valuation seam, net/monetization conventions, the rho choice, the scenario-grid rules.
- `tasks/M3-risk-engine.md` — the workstream spec and bake-off.
