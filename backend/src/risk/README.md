# risk — portfolio Greeks, monetized sensitivities, and scenario stress

TL;DR: pure functions that take positions plus their market state, price each line
through C's frozen pricer, net them into portfolio sensitivities, and stress the
book under explicit scenario states. This is Workstream D (roadmap steps 11–12) —
the payload the backbone exists to produce: trustworthy risk and stress PnL.

```python
from risk import (
    ContractValuationInput, position_risk, aggregate_lines,
    scenario_grid, scenario_line_pnls, worst_case,
)

line = position_risk(portfolio_id="pf-1", quantity=10.0, valuation=valuation)
net  = aggregate_lines(lines, portfolio_id="pf-1", dimension="underlying")
cells = scenario_line_pnls(lines, scenario_grid(scenario_config))
wc = worst_case(cells)   # most negative portfolio PnL + ranked contributors
```

## What it does

1. **Per-position risk (step 11).** `position_risk` builds a `PricingState` from a
   `ContractValuationInput` (via `from_spot`, so the pricer's forward-consistency
   invariant holds by construction) and takes analytic Greeks from the pricer. The
   resulting `PositionRisk` carries its valuation inputs verbatim and exposes
   monetized sensitivities as derived properties, so debugging starts at the line.
2. **Aggregation.** `aggregate_lines` nets lines by `instrument` / `maturity` /
   `underlying`; `aggregate_by_desk` groups by a caller-supplied desk map. Lines are
   summed in canonical (contract-key) order, so the aggregate is a pure function of
   the input set — reordering positions cannot change it. `risk_aggregate` projects a
   group to A's `RiskAggregate`. Same-contract lots are netted first (`net_lots`):
   A's `Position` carries a `source`, so one contract can arrive as several lots, but
   the derived contracts have no lot dimension — the line *is* the contract, so lots
   collapse to one canonical line per `(portfolio, contract)` before any line-level or
   scenario output. Lots that disagree on market state raise `LotConsistencyError`.
3. **Reconciliation.** `reconcile` compares per-unit Greeks against broker-returned
   Greeks and returns only the breaches beyond a versioned threshold; an absent
   (`None`) broker Greek is skipped, not treated as a disagreement, while a
   non-finite (NaN/inf) broker value is surfaced as a breach — corrupt data is not
   agreement.
4. **Scenario stress (step 12).** `scenario_grid` builds a deterministic grid from
   A's versioned `ScenarioConfig`. `full_reprice_pnl` (the source of truth) reprices
   under each shocked state; `local_approx_pnl` is the fast Taylor path. `worst_case`
   returns the largest portfolio loss and its ranked contributors. `scenario_result`
   projects a full-reprice cell to A's `ScenarioResult`.

## Conventions (these are the bugs people hit)

- **Net sensitivities are contract-level**: `net_x = sum(per_unit_x * multiplier *
  quantity)` — share/contract-equivalent, so contracts with different multipliers
  sum coherently. Dollar monetization stays at the line (it is currency-tagged and
  not summed across currencies).
- **Monetization**, consistent with the pricer's per-unit cash Greeks:
  `dollar_delta = delta*spot*M*Q`, `dollar_gamma = gamma*spot²*M*Q` (Eq 17),
  `dollar_vega = vega*0.01*M*Q` (Eq 18, one vol point), `dollar_theta = theta*M*Q`.
- **Scenario shocks**: `spot_shock` relative (`new = spot*(1+shock)`), `vol_shock`
  additive in vol units, `time_shock` a roll-down in years (the discount factor
  rolls at the implied rate). The Taylor time term is `theta * time_shock` with the
  pricer's *negative* calendar theta, so a roll-down loses time value — matching the
  full reprice in sign.
- **One bump source**: every finite-difference perturbation lives in the versioned
  `DEFAULT_BUMPS` (`bumps.py`). The Greeks cross-check and the scenario engine's FD
  path both draw from it, so they cannot silently diverge.

## Invariants

Determinism and provenance on every output: emission adapters take an injected
provenance stamp and read no clock, so a risk row reproduces byte-for-byte in
replay. The scenario grid is versioned by `effective_scenario_version(config)` —
the config section version plus a hash of D's grid-construction constants
(`ROLL_DOWN_DAYS`, the crash rule), so two different grids can never share one
version — and that version is persisted on every `ScenarioResult`. The grid also
de-dupes configured shocks and refuses a residual id collision, so a repeated shock
cannot collapse a cell or double-count the worst case. The headline guarantee is
that worst-case loss regenerates exactly from positions + snapshot + scenario
version. Full reprice is always the reference; the local approximation is a
convenience that agrees only for small shocks (and its divergence on large shocks is
direction-dependent). See ADR 0006 for the non-obvious choices (net convention, rho, the
scenario-grid construction rules) and `../pricing/README.md` for the pricer it
builds on.

## Tests

`tests/test_risk.py` (Greeks vs independent oracle, the analytic-vs-central-diff
cross-check, monetization, aggregation, reconciliation, edges), `test_scenario.py`
(full reprice vs oracle, worst case, small-agree/large-diverge, completeness),
`test_seam_risk.py` (D→A round-trip + D→C interface pin), `test_risk_properties.py`
(reordering invariance, sum-of-lines == aggregate), `test_determinism_risk.py`
(golden, cross-process hashes). Independent oracles: hand-coded GBSM cross-checked
against QuantLib/py_vollib. Branch-coverage floor on `src/risk` is the committed
core floor (`uv run pytest --cov`).
