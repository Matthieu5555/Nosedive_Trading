# T-intent-vs-delivery-audit — hunt the "green gate ≠ correct output" failure class

> **Owner ask (2026-06-12).** Two bugs found this session (tenor selection, delta window) are the
> **same** failure mode, and the worry is there are more. This audit hunts that **one specific
> class** systematically. Framed for dispatched agents. **Audit only — produce findings, do not
> fix.** Each confirmed finding becomes its own remediation task.

## The failure class (precise definition)

> A **config/policy value expresses the economic intent**, but a **separate technical bound, count,
> default, or threshold silently overrides or clips it** — and the **gate stays green** because the
> tests validate the *mechanism* (a self-consistent unit) rather than the *delivered economic
> outcome on real captured data*.

This is NOT "find any bug". It is this one shape. A finding qualifies only if it has: (1) an intended
policy, (2) a technical knob that clips/overrides it, (3) green tests that miss it because they check
the mechanism, not the delivered result.

## Confirmed seed instances (the pattern to generalize from)

1. **Tenor (`T-tenor-selection`, fixed `74d2cc7`).** Intent: pinned grid `10d…3y`. Clip:
   `max_expiries = len(grid)` used only the *count*; `select_expiries` kept nearest-N → front month
   only. Tests checked nearest-N worked, not "spans the grid". QC `tenor_coverage_floor` caught it —
   unread.
2. **Delta (`T-delta-window`, spec'd).** Intent: `delta_bound = 0.30` (±30Δ). Clip:
   `_DISCOVERY_STRIKES_PER_SIDE = 16` (±~1%) qualified far inside the band; docstring even *claimed*
   "superset" without verifying. QC `delta_band_completeness` caught it — unread.
3. **Meta — a check that passes on bad output.** `surface_fit_error` PASSES on the ultra-short SVI
   slices that are degenerate (rho railed, `arb_free=false`) because it scores RMSE, not arb-freeness.
   A QC check with a blind spot is the same class, one level up.

## QC triage context (2026-06-11, already on disk — use as oracle)

9 of 18 QC checks FAIL. `calendar_sanity`, `parity_residual`, `forward_stability` fails all cluster
on the **ultra-short maturities (5–11d)** — symptoms of the tenor root (only short slices captured),
expected to largely clear after a re-capture with real term structure. Two residual items to fold in:
the **ultra-short-slice policy** (flag / down-weight / exclude from calendar+parity?) and a
**label-vs-threshold inconsistency** (`forward_curve` self-labels `residual_mad=0.159` as "good"
while QC fails it at 0.05 — which is authoritative?).

## Method (per agent, per area)

For each economic policy in `TARGET.md` (the domain authority — the blueprint was absorbed into it;
the `documentation/` tree is dead, do not read it) + `configs/`, trace it to code and ask:

1. **Intent located?** Is the policy a typed config value (ADR 0028), not a `.py` literal?
2. **Clip present?** Is there a count / window / cap / `default` / `.get(..., fallback)` /
   truncation / slice that sits between the intent and the delivered output and can silently narrow
   or override it? (Watch especially: `[:N]` slices, fixed per-side counts, `_DISCOVERY_*`-style
   request-shaping bounds, `.get(key, default)` float-key lookups, silent `or`/`if not x: return`.)
3. **Test blind?** Do the tests assert the *delivered economic result on real/realistic data*, or
   only that the mechanism runs? A green test over a hand-built fixture that bypasses the clip = blind.
4. **Caught by QC?** Does a QC check flag a breach — and is anyone reading it? (Coverage panel
   `T-capture-coverage-panel` is the read-path fix.)

Verify each candidate **adversarially on real data** (the 2026-06-11 partitions + the live gateway),
not by re-reading the docstring — the docstrings are part of how these hid.

## Scope (suggested fan-out lanes)

- **Capture / selection** — expiry, strike, delta, moneyness windows; request-shaping bounds; pacing
  caps; snapshot field set. (Seeds 1–2 live here.)
- **Analytics** — forward/parity (the day-count, regression, discount), surface fit + projection
  (pinned-tenor keys, DF lookup, degeneracy handling), Greeks units ($ vs decimal).
- **QC** — every check: does its threshold match the blueprint, and does it have a blind spot like
  `surface_fit_error`? Are thresholds vs self-labels consistent?
- **Risk** — scenario grid shocks, aggregation dimensions, reconciliation tolerances vs the spec.
- **Storage / contracts** — silent partition merges, version leaks, `default`-on-missing.

## Deliverable

A findings table — one row per confirmed instance: `area | file:line | intended policy (source) |
the clip/override | why tests stay green | QC catches it? (check name / no) | severity | suggested
remediation task`. Group by area. Name what was checked and found clean too (so coverage is legible,
not implied). **Do not silently cap the audit** — `log` any area skipped or sampled.

## Why a fresh pass (not the existing 101-finding audit)

The post-capture audit (`AUDIT-POST-CAPTURE-backend-2026-06-11.md`) exists but **missed the roots**:
it logged F-SURF-01 as a discount-rate symptom and F-IBKR-02 as a chronological-sort nit, never
reaching the tenor/delta causes. This audit is framed differently — **intent vs delivered-on-real-
data**, with the live gateway + captured partitions + QC results as the oracle — precisely to catch
what a code-reading audit missed.

## Done criteria

A grouped findings list covering capture / analytics / QC / risk / storage, each finding verified on
real data with the four method questions answered, severity-ranked, with a suggested remediation task
per confirmed item; areas checked-and-clean are named; nothing silently truncated.
