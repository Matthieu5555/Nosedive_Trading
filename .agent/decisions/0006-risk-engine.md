# 0006 — Risk engine: valuation seam, net/monetization conventions, scenario grid

- **Status:** accepted
- **Date:** 2026-06-02

## Context

Workstream D (`src/risk`) is steps 11–12: per-position Greeks, monetized
sensitivities, portfolio aggregation, broker reconciliation, and the scenario
stress engine. It is pure functions over A's contracts built on C's frozen pricing
interface (ADR 0004). Several choices are not obvious from the code and would
otherwise be reverse-engineered or re-litigated by E (which runs risk in the daily
sequence and replays it). They are recorded here.

## Decision

1. **D takes one resolved `ContractValuationInput` per contract, not C's six
   contracts.** D's pure core does not join `MarketStateSnapshot` +
   `ForwardCurvePoint` + surface objects itself; it takes a single typed, owned
   input carrying the resolved market state (spot, carry, vol, df, strike, right,
   style, multiplier, currency, and C's confidence label). This keeps the risk
   functions pure and testable from fixtures and confines the C-contract join to one
   thin assembly step (E's wiring). The state is built through `pricing.from_spot`,
   so `forward == spot * exp(carry * T)` holds by construction — D can never hand the
   pricer an inconsistent state. Spot and carry are the stored anchors; the forward
   is derived.

2. **Net sensitivities are contract-level (`per_unit * multiplier * quantity`);
   dollar monetization stays at the line.** `RiskAggregate.net_*` are
   share/contract-equivalent sums, so contracts with different multipliers aggregate
   coherently — a per-unit sum across multipliers is meaningless. The monetized
   dollar Greeks (`dollar_delta = delta*spot*M*Q`, `dollar_gamma = gamma*spot²*M*Q`
   Eq 17, `dollar_vega = vega*0.01*M*Q` Eq 18, `dollar_theta = theta*M*Q`) live on the
   `PositionRisk` line as derived properties — they are currency-tagged and are *not*
   summed across currencies into the aggregate, because adding USD and EUR dollar
   gamma is a category error. The conventions match the pricer's per-unit cash Greeks
   (ADR 0004 point 1), scaled by multiplier and quantity, so D and C cannot disagree
   on what a cash Greek is. (A's baseline fixture in `fixtures/records.py` happens to
   show net = per_unit*quantity without a multiplier; that is an illustrative record,
   not the convention — this ADR is.)

3. **One versioned bump source.** All finite-difference perturbation sizes live in a
   single versioned `BumpSpec` (`DEFAULT_BUMPS`). Both the Greeks central-difference
   cross-check and the scenario engine's FD local-approximation path import it, so
   they cannot silently diverge — the classic hidden risk/scenario disagreement
   `tasks/04-risk-engine.md` warns about. Made into a test (TESTING.md): the two
   modules reference one object, and the FD and analytic local approximations agree.

4. **rho follows the pricer's forward-fixed convention (`-T * price`), not the
   textbook q-fixed rho.** The pricer (ADR 0004) defines rho holding the forward
   fixed, so only the discount factor responds to the rate. D inherits that: rho is
   not a finite-difference cross-check target, and `central_difference_greeks` fills
   it from the `-T*price` identity. An independent textbook/QuantLib rho (which holds
   the dividend yield fixed) is a different, larger number by design — D does not
   assert against it. Flagged here because a future agent comparing D's rho to a
   broker or QuantLib rho will otherwise think it is wrong.

5. **The scenario grid is built from `ScenarioConfig` plus two D-owned construction
   rules, and the persisted version covers both.** A's `ScenarioConfig` carries
   `spot_shocks` and `vol_shocks` but no time roll-down and no combined-stress
   selection, and D does not edit A's config. So D builds the grid deterministically
   from the config's shocks plus fixed, documented rules: a combined crash (the most
   adverse spot move with the largest vol spike) and a small time roll-down
   (`ROLL_DOWN_DAYS`, Actual/365). The persisted `scenario_version` is *not*
   `ScenarioConfig.version` alone — that would let two different grids share one
   version when a D-owned rule changes (a real reproducibility hole). Instead
   `effective_scenario_version(config)` combines the config section version with a
   SHA-256 hash of the D-owned construction constants (`ROLL_DOWN_DAYS`, the crash
   rule, `GRID_CONSTRUCTION_VERSION`), so changing either the economic shocks or the
   construction rules moves the persisted version automatically. Callers persist that
   effective version on every `ScenarioResult`; a test pins that the version moves
   when a construction constant changes. Duplicate configured shocks are de-duped at
   the source (first-seen order) and a residual id collision raises
   `ScenarioGridError`, so a repeated shock can never silently collapse a cell or
   double-count a scenario in the worst-case total.

6. **Full reprice is the source of truth; the local approximation is a labeled
   convenience.** `ScenarioResult.pnl` is always the full reprice. The Taylor
   approximation is offered for fast intraday checks and tested to agree with full
   reprice for small shocks (rel < 5e-2) and to diverge for a large *adverse* shock
   (rel > 1e-1). Note the divergence is direction-dependent: a large up-move of the
   same size stays closer because gamma curvature aids the expansion, so the test
   asserts the adverse case that matters for risk, not symmetric divergence. The
   approximation never lands in storage as the PnL.

7. **Same-contract lots are netted before any line-level output; the line is the
   contract.** A's `Position` is keyed by `(portfolio_id, contract_key, quantity,
   source)`, so the same contract can legitimately appear as several lots (a broker
   holding plus a hypothetical overlay). But A's *derived* contracts have no lot
   dimension — `RiskAggregate` is net-per-group and `ScenarioResult` is keyed by
   `(portfolio, scenario, contract)` — so two lots of one contract cannot be
   represented as two rows without colliding on their natural key. `net_lots`
   therefore sums the signed quantities of same-`(portfolio, contract)` lots into one
   canonical line, keeping the shared market state, and `aggregate_lines`,
   `aggregate_by_desk`, and `scenario_line_pnls` all net first. This makes every
   line-level and scenario-cell *ordering* a pure function of the input set, not of
   lot arrival order — without it, two lots sorted only by `contract_key` keep caller
   order, and E's byte-identical replay (live vs. stored events, which need not
   preserve position order) would diverge or, worse, pass only because fixtures avoid
   duplicate lots. Netting was chosen over carrying a synthetic `position_key` through
   to the line because the persisted contracts have nowhere to put one; if per-lot
   risk reporting is ever needed, the contract-level fix is to route a lot field into
   A's `RiskAggregate`/`ScenarioResult`, not to make D's ordering lot-dependent. Lots
   that disagree on resolved market state raise `LotConsistencyError` (a corrupt join,
   not something to silently collapse); a net-flat contract is kept as a zero-quantity
   line, not dropped.

## Alternatives considered

- **D consuming C's six contracts directly.** Rejected: it couples D's pure math to
  C's emission shapes and makes every risk test assemble a full analytics pipeline.
  A single resolved input keeps the seam one object wide and the math fixture-driven.
- **Net sensitivities without the multiplier (per-unit * quantity).** Matches A's
  illustrative baseline record but produces an incoherent sum across contracts with
  different multipliers; rejected for the share-equivalent convention.
- **Storing dollar aggregates on `RiskAggregate`.** The contract has no cash or
  currency fields, and cross-currency dollar sums are a category error; monetization
  stays line-level and currency-tagged.
- **Adding `time_shocks` to A's `ScenarioConfig` now.** Cleanest for full grid
  versioning, but it edits a contract D does not own for a roll-down that is, today,
  a single fixed value; deferred to a routed request to A if the need generalizes.
- **A D→C interface pin living only in C.** Per ADR 0004 the breaking pin belongs in
  D's suite (`test_seam_risk.py`) so a C-side change fails D loudly; C keeps a lighter
  shape-pin. The two are complementary.

## Deferred (flagged, not yet done)

Not blockers for E's headline replay, but recorded so they are not lost — natural to
fold into E's QC work or a small follow-up:

- **`reconcile` does not assert `broker.contract_key == line.contract_key`.** A
  mis-wired join would compare the wrong broker Greek to the wrong computed line
  silently. E's "Greek sanity" QC check is the natural place to make the key match a
  hard precondition.
- **`ContractValuationInput` validates only multiplier / currency / confidence.** The
  numeric fields D semantically owns (strike > 0, spot > 0, maturity ≥ 0, vol ≥ 0,
  discount_factor > 0, all floats finite) are left to the pricer; a D-owned check that
  names the offending valuation field would keep the one-object seam debuggable.
- **`scenario_totals` is exported but currently only lightly exercised** — either give
  it a direct multi-cell test or stop exporting it until a caller exists.

## Consequences

- E gets risk and scenario outputs that round-trip through A's store, carry a valid
  provenance stamp, and replay byte-for-byte (the determinism test proves it across
  processes). The byte-identical replay holds even for portfolios with duplicate
  same-contract lots, because `net_lots` makes line and cell ordering independent of
  position arrival order (decision 7) — E does not need to pre-sort positions.
- A C-side change to the pricing state vector, Greeks shape, public surface, or an
  entry-point signature breaks D's `test_seam_risk.py` immediately.
- `src/risk` is added to the `[tool.coverage] source` list with the rest of the pure
  core; the branch-coverage floor (90%, raised never lowered) now covers it.
- The scenario grid regenerates exactly from positions + snapshot + the effective
  scenario version (which now encodes the D-owned construction constants, not just
  the config version); worst-case loss is reproducible, which is D's headline
  guarantee.
