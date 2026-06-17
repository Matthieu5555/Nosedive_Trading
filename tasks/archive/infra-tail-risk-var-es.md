# infra-tail-risk-var-es — intraday VaR / expected shortfall + liquidity & concentration risk

> **Source:** TARGET §5.9 ("what sophisticated desks additionally expect") + the 2026-06-08 autonomy
> audit. **DEFERRED / post-week** — the scenario full-reprice engine and per-book risk aggregation it
> sits on are built; this is the tail-risk layer over them, not week-critical. Spec'd so the §5.9
> risk-engine gap has a home (margin forecasting is already owned by
> [execution-operational-hardening](archive/execution-operational-hardening.md); VaR/ES + liquidity/
> concentration had no task in any lane).
>
> **Provenance note:** VaR / ES is **net-new scope traced to TARGET only** — it is **not** in the
> blueprint (which expresses risk purely as Greeks + scenario PnL, never a VaR/ES/confidence-level
> measure) and **not** in the course transcripts. Defensible (it is standard desk risk) and deferred,
> but it does not inherit from either reference doc; if it is ever pulled forward, fold the VaR/ES
> definition into the blueprint via an amendment first (ADR 0011) rather than treating it as already-blessed.

## The gap
§5.9 names three risk-engine capabilities with no task: **intraday VaR / expected shortfall**,
**liquidity risk**, and **concentration risk**. The substrate exists — `risk/scenarios.py`
(`full_reprice_pnl`, the ADR-0006 oracle), the spot×vol×rate stress grid (2B + landed rate axis),
`risk/aggregation.py` (per-underlying/family book aggregation) — but there is no VaR/ES estimator and
no liquidity/concentration screen reading them.

## Scope (when it opens, post-week)
- **VaR / Expected Shortfall** at a book/strategy level, computed off the **full-reprice** scenario
  distribution (historical and/or parametric over the banked surface history) — never a naive
  delta-normal approximation when the book is option-heavy; ES (CVaR) is the headline for an
  options book with fat left tails (S2's short-put line is the motivating case).
- **Concentration risk:** exposure concentration by underlying / sector / expiry / Greek (e.g. net
  vega in one name or one tenor) — reuses the `risk/aggregation.py` axes.
- **Liquidity risk:** position size vs captured option volume / open interest
  ([[ibkr-option-volume-capture]] is the data input) — flag positions that cannot be exited inside a
  bounded participation rate.
- Contract-typed, as-of, book-additive where the metric allows; surfaced for the risk screen.

## Depends on / sequence
Reads the landed full-reprice scenario engine + `risk/aggregation.py`. Liquidity needs
[[ibkr-option-volume-capture]]. Tail metrics over history need banked surface/P&L depth, so this is
naturally post-week (pairs in spirit with [[infra-named-scenarios-and-corr-shock]] — named stress and
VaR/ES are the two halves of the §5.4/§5.9 risk-manager screen). Distinct from
[execution-operational-hardening](archive/execution-operational-hardening.md), which owns margin forecasting
and the kill switch.

## Done criteria
Book/strategy VaR + ES off the full-reprice distribution (method documented, oracle-tested on a
hand-checked tiny book); concentration metrics on the aggregation axes; a liquidity flag against
captured volume; contract-typed and as-of; `check-lookahead-bias` clean on any historical path; gate
green.

## Tech-lead assessment (Surface & Analytics family, 2026-06-17) — deferred + amendment-gated, not started
Confirmed the substrate is real and buildable: `risk/scenarios.py` (`full_reprice_pnl`,
`scenario_line_pnls`, `scenario_totals`) and `risk/aggregation.py` (`aggregate_by_key`/`risk_aggregate`
over grouping dimensions) are landed. **But this task is deliberately not actionable now:**
- **Post-week by design** — board + spec header + TARGET §5.9 all class it "what sophisticated desks
  *additionally* expect," explicitly deferred; nothing signals a pull-forward.
- **Blueprint-amendment gated (ADR 0011).** Per this spec's own provenance note, VaR/ES is **not** in
  the blueprint or the course transcripts; "if it is ever pulled forward, fold the VaR/ES definition
  into the blueprint via an amendment first rather than treating it as already-blessed." That gate is
  unmet — the same posture as the rates-curve ingest (ADR 0054).
Verdict: **not started** — deferred and owner/amendment-gated. The concentration/liquidity sub-slices
are bound to the same post-week umbrella and headline (VaR/ES), so they are not split off as separate
authorized work.
