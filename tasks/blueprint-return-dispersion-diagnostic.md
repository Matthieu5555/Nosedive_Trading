# Task — Return dispersion to the blueprint: realized-vol ρ̄, retire constituent-option capture

**Status:** queued (2026-06-15). **Owner ruling set by Vincent (2026-06-15).**
**Decision of record:** [`ADR 0051`](../.agent/decisions/0051-return-to-blueprint-dispersion-realized-vol-diagnostic.md)
(supersedes 0045, partially 0044). Resolves OQ-12.
**Lane:** `infra-` (signal rewire) + `ibkr-` (retire the capture lane) + `core-` (drop the capture knob).

> ⏸ **DO NOT START before the 2026-06-15 evening close run.** Owner: the live capture path must not
> change before tonight's run. Tonight runs unchanged (top-10), one last time. This task lands
> *after* tonight's close is captured and validated.

## Why

The constituent-option-capture lane (ADR 0045) baked a *strategy* choice into the *immutable* raw
layer — causing permanent option-history loss for un-captured names and the serial-capture
throughput crisis — for a feature the **blueprint computes from data we already hold**. Eq. 23
(`02-math-framework.md:129`) needs constituent **volatilities** + weights (or an average-correlation
assumption), explicitly a "diagnostics primitive, not strategy logic" — never constituent *implied*
vols, never captured constituent chains. The blueprint captures the index's **options** and the
underlyings' **prices**. See ADR 0051 for the full argument and the realized-vol caveat.

## Scope of change (all deferred until after tonight)

1. **`infra` — rewire ρ̄ to realized constituent vol.** In `infra/signals/signal_set.py`, feed
   `implied_correlation`'s `constituent_vols` from `realized_vol_by_subject` (already computed for
   the index + **all** constituents from `daily_bar`, lines 181/212-224) instead of the
   constituent **implied** ATM vols from surfaces (top-10 only). Keep `index_vol` = the index's
   implied ATM vol (hybrid implied/realized ρ̄, Eq. 23). This removes the `universe.yaml:88` top-10
   bias at its source. `implied_correlation` itself (`correlation.py`) is unchanged — it is already
   agnostic to the vol's origin. Adjust the signal-kind label / docstring to state the hybrid basis
   honestly. Update the ρ̄ tests to the realized-vol inputs.
2. **`infra-ibkr` — retire the constituent-option-capture lane.** Remove
   `collectors/cp_rest_constituent_capture.py` and its wiring in `live_basket_source` /
   `scripts/eod_run.py`; the close fire reverts to the index leg
   (`cp_rest_close_capture.collect_live_basket`). The full-membership OHLC backfill
   (`history_backfill.py`) and the index close-capture are **unchanged**.
3. **`core` — drop the capture knob.** Remove `UniverseConfig.constituent_top_n` and its
   `configs/universe.yaml` entry (the *capture* gate). **Keep `dispersion_top_n`** — it survives as
   a pure strategy-side selector over banked raw (ADR 0044 partial-supersede). Regenerate the
   `universe` config-hash golden by design.
4. **Contracts / storage:** the `constituent_capture_outcomes` table and the
   `ConstituentCaptureOutcome` contract (from the EMERGENCY-constituent-lane work) lose their
   producer — decide retire-vs-keep-dormant in the PR (lean retire; additive removal, golden regen).
5. **Front:** the coverage panel's constituent rows go empty — drop the constituent coverage column
   or relabel honestly.

## Lanes this moots

ADR 0051 dissolves the option-capture throughput emergency. On landing, retire / re-scope:
`ibkr-capture-cross-underlying-concurrency`, `ibkr-snapshot-warmup-concurrency`,
`ibkr-intraday-conid-cache`, `EMERGENCY-capture-throughput`, `EMERGENCY-constituent-lane-activation`
— any residual value is only the index's *own* chain (already inside the window).

## Out of scope / deferred

Trading a *pure implied-correlation* dispersion (single-name option straddles) would re-open
constituent-option capture with its own capture-cost / IBKR-entitlement / throughput case — a new,
separately-ruled decision, **not** assumed here.
