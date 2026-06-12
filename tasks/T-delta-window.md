# T-delta-window — the discovery strike window must CONTAIN the 30Δ band, not clip it

> **Owner ask (2026-06-12).** The professor asked for a **±30Δ** band; the economic policy
> (`delta_bound: 0.30`) is correct — but a separate technical bound silently clips it to ~ATM±1%
> at every tenor. Direct follow-up to [T-tenor-selection](T-tenor-selection.md) (same file /
> functions, landed `74d2cc7`); the two go together. **Spec only — implementation by a dispatched
> agent.**

## The bug (verified on real 2026-06-11 SPX data, spot 7392)

Two separate knobs, conflated and miswired:

- `delta_bound: 0.30` (`configs/universe.yaml`) — the **economic policy**, exactly the ±30Δ asked.
  Correct.
- `_DISCOVERY_STRIKES_PER_SIDE = 16` (`cp_rest_close_capture.py:98`) — a **request-shaping cap**:
  discovery qualifies only the 16 nearest strikes per side (`_nearest_strikes`), then
  `secdef/info` resolves only those. Its docstring claims it is "a superset of the downstream 30Δ
  band". **That claim is false.** `select_strikes_delta_band` (`chain_planning.py`) can only pick
  from what discovery qualified, so the 30Δ band is hard-capped by the ±16 window.

±16 strikes at 5-pt spacing = **±78 pts (±1.05%)**. The 30Δ call strike is *further out than that
at every tenor*, and the gap explodes with maturity:

| Tenor | 30Δ call distance | Captured reach | |
|-------|-------------------|----------------|---|
| 10d | +99 pts (1.3%) | ±78 pts | clipped |
| 1m | +177 (2.4%) | ±78 | clipped (~half) |
| 3m | +318 (4.3%) | ±78 | clipped |
| 6m | +467 (6.3%) | ±78 | clipped |
| 1y | +695 (9.4%) | ±78 | clipped |
| 2y | +1058 (14.3%) | ±78 | clipped hard |
| 3y | +1370 (18.5%) | ±78 | clipped hard |

(σ≈0.15; 30Δ strike ≈ `F·exp(0.524·σ√T + ½σ²T)`.) So we deliver a ~±1% sliver, not ±30Δ, at all
but the very front. The QC **already flags it**: `delta_band_completeness` = **FAIL** (measured 8)
on 2026-06-11. The intent is right; the *delivery* is clipped, silently, gate green.

**Now urgent:** T-tenor-selection just unlocked the long tenors — precisely where the 30Δ band is
widest (±14–18% at 2–3y). Without this fix we would capture a full term structure of ±1% slivers.

## Objective

Make the **discovery** strike window delta-driven and tenor-aware: wide enough to *contain* the 30Δ
band (plus a safety margin) at each captured tenor, so `select_strikes_delta_band` can actually
reach the 30Δ put and call. Replace the fixed ±16 count. Leave `delta_bound` (the policy) untouched.

## Owns

- The discovery strike qualification in `cp_rest_close_capture.py` (`_discover_chain` /
  `_nearest_strikes`, the `_DISCOVERY_STRIKES_PER_SIDE` bound). Possibly a small broker-neutral
  helper in `chain_planning.py` to compute the strike bounds from a target delta.
- Coordinates with the **already-landed** T-tenor-selection (same file/functions) — rebase clean.

## Key design decision (owner — the pacing tension)

The ±16 existed to bound pacing: `secdef/info` is one paced call per (strike, right). The true 30Δ
band at 3y spans ~±1400 pts; even at wider long-dated strike spacing that is ~100+ strikes/side =
hundreds of paced calls **per tenor**. So the agent must choose (and the owner should rule):

- **Full 30Δ everywhere** — accept a longer capture (it is a once-daily EOD job; minutes are fine).
- **Per-tenor cap + labeled clip** — qualify up to the 30Δ band but cap strikes/side at a generous
  bound; when the cap clips the band, emit a **labeled** coverage gap (so QC / the coverage panel
  show it honestly, never silently). Recommended default unless the owner wants full 30Δ.

Secondary problem to solve: the **working vol is not known at discovery** (the fitted vol is
downstream). Use a conservative per-index working vol (config, not a `.py` literal — C7 / ADR 0028)
or infer a window from listed-strike density; over-qualifying slightly is fine, under-qualifying
re-creates the clip. Do **not** read a wall clock or any look-ahead input.

## What to do (ordered)

1. Compute, per captured tenor, the strike bounds that contain the 30Δ put and 30Δ call (target
   `delta_bound` + margin) from spot/forward and a conservative working vol; qualify all listed
   strikes inside, replacing the fixed `_nearest_strikes(..., 16)`.
2. Apply the pacing policy chosen above; when a per-tenor cap clips the band, record a labeled
   coverage gap on the chain/plan (feeds 1H QC `delta_band_completeness` + the coverage panel).
3. Keep `select_strikes_delta_band` and `delta_bound` unchanged — discovery now feeds it a true
   superset, so the economic 30Δ selection finally bites.
4. No look-ahead; deterministic (replay-stable, ADR 0027); config-driven working vol (ADR 0028).

## Test surface

Read `tasks/TESTING.md`; independent oracles.

- **Contains the band — independent oracle.** For a tenor where the 30Δ strike is provably beyond
  ±16 listed strikes (hand-compute the 30Δ boundary via `scipy.norm.ppf`, a different path than the
  engine), assert the discovery window now includes those strikes and `select_strikes_delta_band`
  returns the full 30Δ block — not the ±16 truncation.
- **Pacing cap + labeled clip.** With a per-tenor cap below the band width, assert the window stops
  at the cap **and** emits a labeled coverage gap (not a silent truncation).
- **Short tenor unchanged-ish.** At 10d the window is small; assert it still contains the (narrow)
  30Δ band and did not balloon.
- **Determinism / no look-ahead.** Same inputs → same qualified set; `check-lookahead-bias` clean.
- **Edge cases.** Thin listing, single strike, working vol missing/garbage → labeled outcome.
- Gate green: `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`.

## Done criteria

The discovery strike window contains the 30Δ band (+margin) at every captured tenor up to the agreed
pacing cap; any residual clip is **labeled** (QC + coverage panel show it); `delta_band_completeness`
QC clears for covered tenors after a re-capture; `delta_bound` policy untouched; deterministic, no
look-ahead, gate green.

## Gotchas

- **The "superset" docstring was a false claim — don't reinstate a fixed count.** A flat
  strikes-per-side bound cannot contain a band whose width grows with √T. The window must scale with
  the tenor.
- **Pacing is the real tension, not a detail.** Be explicit about the budget; `log()` any drop. A
  silent cap reads as "we captured the band" when we didn't — that is the exact failure mode this
  task exists to kill.
- **Re-capture to see it.** Like T-tenor-selection, this changes selection; the band only fills on
  the next capture run.
- **One delta source.** Read delta from the pricing engine (as `select_strikes_delta_band` does);
  the discovery working-vol estimate is only for *bounding the window*, never the economic selection.
- **uv only.**
