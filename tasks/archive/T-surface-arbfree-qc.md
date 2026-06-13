# T-surface-arbfree-qc — surface_fit_error gates on RMSE only, blind to arbitrage/degeneracy

> **✅ DONE 2026-06-13.** `check_surface_fit_error` (`qc/checks.py`) now PASSes only when
> `rmse ≤ max_surface_rmse` **AND** `arb_free` **AND** no `bound_hits` **AND** `converged is not
> False` (`converged is None` = the non-SVI fallback, unknown, not penalised). Labelled
> `degeneracy_reasons` (`arb_violation` / `bound_hit:<param>` / `not_converged`) ride in the
> context. No new config field (existing `SliceFit` flags + `max_surface_rmse`) → no config-hash
> change. Regression in `test_qc_checks.py` locks the real-SPX case (tiny RMSE + `arb_free=False`
> / `rho` railed → FAIL). Gate green (ruff/mypy 210/pytest EXIT 0). Archive-ready.

> **From the 2026-06-12 intent-vs-delivery audit** ([report](AUDIT-INTENT-VS-DELIVERY-2026-06-12.md),
> findings An-3 / QC-2). This is **seed #3** of the green-gate≠-correct class, confirmed on real
> data. **Not a duplicate of the vol-surface lane:** that lane already landed the *propagation*
> (`arb_free`/`bound_hits`/`degeneracy_reasons` now flow to the contract + BFF + a visible smile
> flag — [T-vol-surface-correctness](archive/T-vol-surface-correctness.md), policy = flag-not-reject).
> **What is still open is the QC gate that should consume those flags** — it doesn't.

## The bug (verified on real 2026-06-11 SPX diagnostics)

`check_surface_fit_error` (`packages/infra/.../qc/checks.py:370-403`) computes only:

```
status = STATUS_PASS if fit.rmse <= thresholds.max_surface_rmse else STATUS_FAIL   # :384
```

against `qc.yaml:60 max_surface_rmse: 0.02`. It never inspects the `arb_free` / `bound_hits` /
`butterfly_violations` fields that `SliceFit` now carries. Result on real data: **all 4 SPX slices
PASS** (rmse ~6e-6) while **3 of 4 are `arb_free: False`** — slice 0 has `rho = -0.999` (railed to
the `pricing.yaml:20` SVI bound) and slice 2 has `sigma = 0.0000` (degenerate). A degenerate /
arb-violating fit scores a tiny RMSE precisely because it is over-fit, so RMSE-only **rewards** the
pathology.

## Why the gate stayed green

Tests assert RMSE pass/fail on clean fixtures and never assert that an `arb_free=False` /
bound-railed slice is flagged. The check is self-consistent on its one metric — it just measures the
wrong thing for "is this smile usable".

## Fix direction

- Make `check_surface_fit_error` (or a sibling check) **FAIL or WARN** when `arb_free == False`, when
  a parameter sits on its bound (`bound_hits` non-empty, e.g. rho at ±0.999), or when a slice is
  degenerate (sigma→0). Thresholds/policy in typed config.
- Decide the QC policy vs the surface lane's flag-not-reject rendering policy: the surface may still
  *render* a flagged smile, but **QC must not report it as a clean fit**.
- Add a real-data regression: a degenerate/railed slice must produce a non-PASS QC verdict.

## Done criteria

`surface_fit_error` consumes `arb_free`/`bound_hits` and does not PASS a railed/degenerate slice; a
regression locks it on the 2026-06-11-style data; gate green.
