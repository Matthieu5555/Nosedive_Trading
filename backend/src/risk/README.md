# risk — portfolio Greeks, monetized sensitivities, and scenario stress

TL;DR: pure functions that take positions plus their resolved market state, price
each line through C's frozen pricer, net the lines into portfolio sensitivities,
reconcile against the broker, and stress the book under explicit shocked market
states. This is Workstream D (roadmap steps 11–12) — the payload the whole backbone
exists to produce: trustworthy risk and worst-case stress PnL. This README is the
reference and explanation for `src/risk`; for the non-obvious design choices behind
it (the net convention, rho, the scenario-grid rules) read ADR 0006, which this
points to rather than restates.

```python
from risk import (
    ContractValuationInput, position_risk, aggregate_lines,
    scenario_grid, scenario_line_pnls, worst_case, reconcile,
)

line  = position_risk(portfolio_id="pf-1", quantity=10.0, valuation=valuation)
net   = aggregate_lines([line], portfolio_id="pf-1", dimension="underlying")
cells = scenario_line_pnls([line], scenario_grid(scenario_config))
wc    = worst_case(cells)   # most negative portfolio PnL + ranked contributors
```

## Why it exists

Steps 1–10 turn raw IBKR ticks into a clean, priced market state. Risk is the last
layer: it takes the positions you actually hold, asks "what is each one worth and how
does it move," and then "what do I lose if the market drops 5% and volatility
spikes." That worst-case loss is the headline number the backbone produces, and the
non-negotiable property is that it regenerates *exactly* — same positions, same
snapshot, same scenario version always give the same loss, live or in replay.

Everything here is pure functions over A's typed contracts. There is no clock read,
no I/O, no hidden state. That is what lets E run the identical code live and in
replay and get byte-identical rows.

## The boundary: risk never prices

The single most important thing to understand is what D does *not* do. D never
implements an option-pricing formula. It builds the pricer's `PricingState` and calls
C's frozen `price()` for every value and every Greek — analytic for European, the
binomial lattice for American. C owns pricing; D owns *position* math (scaling by
multiplier and quantity), aggregation, reconciliation, and the scenario grid.

That boundary is enforced by construction. D's input is one resolved
`ContractValuationInput` per contract, not C's six analytics contracts; D builds the
pricing state through `pricing.from_spot`, so the pricer's invariant
`forward == spot * exp(carry * T)` holds automatically and D can never hand the pricer
an inconsistent state. The C-contract join lives in exactly one thin assembly step,
which is E's wiring, not D's math. A C-side change to the pricing state vector, the
Greeks shape, or an entry-point signature breaks D's `tests/test_seam_risk.py`
immediately and loudly. See `../pricing/README.md` for the pricer and ADR 0004 for the
frozen interface.

## Data flow

```text
ContractValuationInput  (one per contract: spot, carry, vol, df, strike,
        |                right, style, multiplier, currency, confidence)
        |  pricing_state_for -> pricing.from_spot
        v
  C's frozen price()  ->  PriceGreeks (per-unit price, delta, gamma, vega, theta, rho)
        |
        v
   PositionRisk  (the line: inputs carried verbatim + per-unit Greeks;
        |         position_* and dollar_* are derived properties)
        |
        +--> net_lots ----> aggregate_lines / aggregate_by_desk
        |                        |  risk_aggregate
        |                        v
        |                   contracts.RiskAggregate   (persisted, stamped)
        |
        +--> reconcile(broker) -> list[GreekDiscrepancy]   (breaches only)
        |
        +--> scenario_grid(config) -> scenario_line_pnls -> worst_case
                                           |  scenario_result
                                           v
                                  contracts.ScenarioResult   (persisted, stamped)
```

This shows the normal flow from one resolved market state to the two persisted
contracts. It omits the provenance stamp, which is built by the caller (E) with an
injected `calc_ts` and passed into `risk_aggregate` / `scenario_result` — D itself
reads no clock. The flow starts with one `ContractValuationInput` per held contract;
`position_risk` turns it into a `PositionRisk` line by calling the pricer. From the
line, three independent paths fan out: aggregation nets lines into portfolio
sensitivities, reconciliation compares per-unit Greeks against the broker, and the
scenario engine reprices the line under each shocked state. The two emission adapters
(`risk_aggregate`, `scenario_result`) project D's internal types onto A's persisted
contracts.

## The line and its sensitivities (step 11)

`position_risk` returns a `PositionRisk`: the `ContractValuationInput` carried
verbatim plus the per-unit `PriceGreeks` from C. Everything monetized is a derived
property, so it cannot drift from the per-unit Greeks it scales. The line's scale
factor is `multiplier * quantity` (signed), and there are two families of derived
sensitivity, in different units.

Position-level (`position_delta`, `position_gamma`, `position_vega`,
`position_theta`) are `per_unit * multiplier * quantity` — share/contract-equivalent.
These are what aggregate, because a per-unit sum across contracts with different
multipliers would be meaningless.

Dollar-monetized are currency-tagged cash figures and live only on the line; they are
*not* summed into the aggregate (adding USD and EUR dollar gamma is a category error).
Their units, matching the pricer's per-unit cash Greeks scaled by `M*Q`:

| Property       | Formula                       | Per what move                  |
| -------------- | ----------------------------- | ------------------------------ |
| `dollar_delta` | `delta * spot * M * Q`        | cash PnL per 1.00 spot move    |
| `dollar_gamma` | `gamma * spot² * M * Q`       | dollar gamma (Eq 17)           |
| `dollar_vega`  | `vega * 0.01 * M * Q`         | cash PnL per one vol point (Eq 18) |
| `dollar_theta` | `theta * M * Q`               | cash PnL per year of decay     |

`central_difference_greeks` is the independent cross-check, not the production path: it
central-differences the *pricer's own price* using the shared bumps (see below). The
test that analytic and central-difference Greeks agree is what catches a sign or unit
error. `rho` is special — it is filled from the forward-fixed identity `-T * price`,
not differenced, because the pricer defines rho holding the forward fixed (ADR 0006
point 4). A broker or QuantLib rho holds the dividend yield fixed instead and is a
different, larger number *by design*; D does not assert against it.

## Aggregation

`aggregate_lines` groups lines by an intrinsic `dimension` — `"instrument"`,
`"maturity"`, or `"underlying"` — and nets each group into `NetSensitivities`, which
keeps its contributing lines so the result stays explainable. `aggregate_by_desk`
groups by a caller-supplied `contract_key -> desk` map; an unmapped contract falls
into `"desk:unassigned"` rather than being dropped. `risk_aggregate` projects one
group onto A's `RiskAggregate`.

Two invariants hold by construction: the sum of the lines equals the aggregate, and
the aggregate is a pure function of the input *set*, not of position arrival order.
Order-independence comes from netting same-contract lots first (`net_lots`) and then
sorting by contract key. A's `Position` carries a `source`, so one contract can arrive
as several lots (a broker holding plus a hypothetical overlay), but the derived
contracts have no lot dimension — the line *is* the contract. `net_lots` sums the
signed quantities, keeps the shared market state, and returns one line per
`(portfolio, contract)`. A net-flat contract is kept as a zero-quantity line, not
dropped (its presence in the book is a fact). This order-independence is exactly what
E's byte-identical replay depends on; ADR 0006 point 7 explains why netting was chosen
over carrying a per-lot key.

## Reconciliation

`reconcile(line, broker)` compares D's per-unit Greeks against broker-returned ones
and returns only the breaches — the Greeks whose absolute difference exceeds a
versioned per-unit threshold (`DEFAULT_RECON_TOLERANCE`: delta/gamma 1e-3, vega/theta
1e-2). An empty list means everything agreed. A mismatch means D's pricing of the
contract and the broker's disagree by more than tolerance on that Greek: in practice
either the resolved market state (spot, vol, carry) differs from what the broker
used, or there is a genuine pricing-model gap worth investigating. Each
`GreekDiscrepancy` carries the computed value, the broker value, the absolute
difference, the threshold, and the threshold version, so the breach is fully
traceable. Note `reconcile` compares whatever broker Greek you hand it for a line and
does not itself assert `broker.contract_key == line.contract_key`; ADR 0006 flags that
key-match as belonging to E's "Greek sanity" QC check.

Two edge cases matter. An absent broker Greek (`None`) is skipped — the broker not
returning a value is not a disagreement. A non-finite broker value (NaN/inf) is
surfaced as a breach — corrupt data is not agreement, and without the explicit check
`nan > threshold` is `False`, so a NaN would otherwise read as "agrees."

## Scenario stress (step 12)

A scenario is an explicit shocked market *state*, never a Greek multiplier.
`scenario_grid(config)` builds a deterministic grid from A's `ScenarioConfig`
(`spot_shocks`, `vol_shocks`) plus two D-owned construction rules: a combined crash
(the most adverse spot move paired with the largest vol spike) and a small time
roll-down (`ROLL_DOWN_DAYS`, Actual/365). `scenario_line_pnls` produces every
`(scenario, line)` cell — the cartesian product, so completeness is structural —
and `worst_case` returns the scenario with the largest portfolio loss plus its lines
ranked worst-first. `scenario_result` projects a full-reprice cell onto A's
`ScenarioResult`.

The shock conventions, asserted by tests:

- `spot_shock` is relative: `new_spot = spot * (1 + spot_shock)`.
- `vol_shock` is additive in vol units, floored at zero: `new_vol = max(vol + vol_shock, 0)`.
- `time_shock` is a roll-down in years: `new_T = max(T - time_shock, 0)`, and the
  discount factor rolls to the shortened maturity at the implied rate.

Carry is held fixed through a shock, so the forward tracks the shocked spot — the same
state the pricer would see on that day in that market.

### Worked example: the "-5% spot, +vol" stress

Take BIG_PICTURE.md's headline question: what do I lose if the market drops 5% and
vol spikes. Suppose the config carries `spot_shocks` including `-0.05` and `vol_shocks`
including `+0.05` (five vol points). The grid's combined-crash cell is built from the
most adverse spot move and the largest vol spike, so it is the scenario
`crash_spot-0.0500_vol+0.0500` with `spot_shock=-0.05, vol_shock=+0.05, time_shock=0`.

For a long call line with spot 100, vol 0.20, multiplier 100, quantity 10,
`shock_valuation` produces the shocked state spot `95.0`, vol `0.25`, maturity
unchanged. `full_reprice_pnl` reprices the line under that state through C's pricer and
returns `(shocked_price - base_price) * (100 * 10)`. That full reprice is the source
of truth and the number that lands in `ScenarioResult.pnl`. The `-5%` spot move loses
delta value while the `+5` vol points add vega value; the net depends on the option,
and the full reprice captures the curvature (gamma, vanna) that a Greek-multiplier
shortcut would miss.

### Full reprice vs. the local approximation

`full_reprice_pnl` is always the source of truth and the only thing persisted.
`local_approx_pnl` is a fast Taylor (Eq 19) path for intraday checks, using the line's
analytic Greeks:
`delta*dS + 0.5*gamma*dS² + vega*vol_shock + theta*time_shock`, times scale. It is
tested to agree with full reprice for small shocks (rel < 5e-2) and to *diverge* for a
large adverse shock (rel > 1e-1). The divergence is direction-dependent: a large
up-move of the same size stays closer because gamma curvature aids the expansion, so
the test asserts the adverse case that matters for risk. `local_approx_pnl_fd` is the
same Taylor expansion but with finite-difference Greeks for instruments whose analytic
Greeks are not trusted; it draws its bump from the same shared source. The
approximation never lands in storage.

## One bump source

Every finite-difference perturbation in the engine lives in one versioned `BumpSpec`,
`DEFAULT_BUMPS` (`bumps.py`). Both the Greeks central-difference cross-check and the
scenario FD path import it, so they cannot silently diverge — the classic hidden
risk/scenario disagreement. The units, stated once: `spot_first_rel` and
`spot_second_rel` are *relative* spot fractions (delta wants a small bump, gamma a
larger one to clear float noise); `vol_abs` is additive in vol units; `time_abs` is
additive in years. Changing a bump is a deliberate, reviewable bump of `BUMP_VERSION`.

## Determinism and versioning

Every emission adapter takes an injected provenance stamp and reads no clock, so a
risk row reproduces byte-for-byte in replay. The scenario grid is versioned by
`effective_scenario_version(config)` — the config section version folded with a hash
of D's grid-construction constants (`ROLL_DOWN_DAYS`, the crash rule,
`GRID_CONSTRUCTION_VERSION`) — *not* `config.version` alone, which would let two
different grids share one version (a reproducibility hole). That effective version is
persisted on every `ScenarioResult`, so worst-case loss regenerates exactly from
positions + snapshot + scenario version. The grid also de-dupes configured shocks
(first-seen order) and refuses a residual id collision, so a repeated shock can never
collapse a cell or double-count the worst case. ADR 0006 has the full rationale.

## Failure modes

The risk core is pure, so the failures are input-validation and corrupt-join errors,
not transient I/O. None of these are retryable in the network sense — the caller fixes
the input.

| Raised by | When | What it means / what to do |
| --------- | ---- | -------------------------- |
| `ValuationError` | `ContractValuationInput` has a non-positive/non-finite `multiplier`, an empty `currency`, or a `confidence` not in `("ok", "low")` | Malformed valuation input; carries the field, value, and reason. Fix the upstream join. Note the numeric fields (strike, spot, maturity, vol, df) are validated by the pricer, not here (ADR 0006 deferred item). |
| `LotConsistencyError` | Two lots of one `(portfolio, contract)` disagree on resolved market state | A corrupt join — the same contract at one snapshot must have one market state. Netting would silently pick one, so it raises. Carries portfolio and contract key. |
| `AggregationError` | `aggregate_lines` called with a dimension outside `("instrument", "maturity", "underlying")` | Programming error in the caller; carries the bad dimension. |
| `ScenarioGridError` | The grid has colliding scenario ids after de-dup (a precision collision of distinct shocks) | The config's shocks format to the same id; surfaced loudly rather than collapsing a cell. Adjust the shocks. |
| `ValueError` | `worst_case` called with an empty cell set | A worst case over nothing has no answer. Pass a non-empty grid. |

A low-confidence contract is *not* a failure: C's `"low"` quote-QC label rides through
on the line so it can be surfaced, and the position is still priced.

## Fastest way to exercise it

```bash
cd backend && uv run pytest tests/test_risk.py tests/test_scenario.py -q
```

`tests/test_risk.py` covers step 11 (Greeks vs. an independent oracle, the
analytic-vs-central-difference cross-check, monetization, aggregation, reconciliation,
edges); `tests/test_scenario.py` covers step 12 (full reprice vs. oracle, worst case,
small-agree / large-diverge, completeness). `tests/test_seam_risk.py` pins the D→A
round-trip and the D→C pricer interface; `tests/test_risk_properties.py` is the
reordering-invariance and sum-of-lines-equals-aggregate property suite;
`tests/test_determinism_risk.py` is the golden and cross-process-hash determinism
suite. Independent oracles are a hand-coded GBSM model cross-checked against
QuantLib / py_vollib. `src/risk` sits under the committed branch-coverage floor; run
the full gate with `uv run pytest --cov`.

## See also

- `../pricing/README.md` — C's frozen pricer that D builds on, and the cash-Greek conventions.
- ADR 0006 (`.agent/decisions/0006-risk-engine.md`) — the valuation seam, net/monetization conventions, the rho choice, and the scenario-grid construction rules.
- ADR 0004 — C's frozen pricing interface that D pins.
