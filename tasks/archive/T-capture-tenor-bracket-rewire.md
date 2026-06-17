# T-capture-tenor-bracket-rewire — re-wire tenor-targeted expiry selection into the capture default

> **P1 — latent; becomes P0 the first time capture runs against a chain with >64 listed
> expiries (SPX, or a weekly-heavy SX5E day).** Source: F1 of
> [T-intent-vs-delivery-audit-findings-2026-06-16](T-intent-vs-delivery-audit-findings-2026-06-16.md).
> Owns the capture-selection seam — coordinate with [infra-strike-window-pct-clip](infra-strike-window-pct-clip.md)
> (same module, `chain_planning.py`); serialize, do not run in parallel.

## The defect (verified live 2026-06-17)

`_selection_from_config` (`packages/infra-ibkr/.../collectors/cp_rest_close_capture.py:636-639`)
builds a `ChainSelection` with **only** `max_expiries=_CAPTURE_ALL_MATURITIES_BUDGET=64` —
`tenor_years`/`as_of` are left empty, so `is_tenor_targeted()` is False and both selection paths
fall through to the nearest-N slice (`select_expiries → unique[:64]` /
`_nearest_expiries(group, 64)` in `infra/.../universe/chain_planning.py:118-120,348-353,402-430`).

Commit `38910d9` **reverted** the tenor-bracketing wiring that the tenor-seed fix `74d2cc7` had
added. The "64 is far above any real chain" rationale is unverified and wrong as a primitive:
nearest-N at any finite budget front-loads weeklies and **silently drops the LEAPs**. Dormant on
the 2026-06-16 SX5E day (29 expiries < 64); would bite SPX or a weekly-heavy day.

`tenor_coverage_floor` QC would flag it — but the failure is currently **unread**.

## Fix

Either (a) re-wire tenor-targeted bracketing into `_selection_from_config` (resolve the pinned
`tenor_grid` 10d…3y into `tenor_years` + `as_of` so `is_tenor_targeted()` is True), **or**
(b) if "keep every listed maturity" is the real intent, switch the primitive from nearest-N to
keep-all/bracket and **prove** the budget exceeds the longest real chain on SPX.

## Done criteria

- Production `_selection_from_config` default no longer relies on nearest-N truncation for the
  long end.
- A **delivered-result** test on a **>64-expiry** chain asserts the LEAPs survive selection (the
  existing `test_tenor_selection` fixtures are ≤8 expiries and never exercise the prod default).
- Verify on a real captured partition that 2y/3y slices are present (subject to two-sided
  liquidity — see F2, a separate QC-floor concern).

Then archive this task.
