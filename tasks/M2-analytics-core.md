# M2 — Analytics core (snapshots → forwards → IV → surfaces → pricing)

- **Branch:** `feat/merge-analytics`
- **Owns:** `packages/infra/src/algotrading/infra/{snapshots,forwards,iv,surfaces,pricing,utils}/**` (+ READMEs).
- **Depends on:** M0 (frozen contracts + analytics dataclass seam).
- **Blocks:** M3 (pricing interface), M7 (the actor drives these), M8 (surfaces API).

## Objective

This is the largest pure bake-off. Both repos built the same analytics, module for module, as pure functions. Diff the two implementations per module and keep the better one — correctness first, then depth, then clarity. No I/O, no clock, no RNG; everything stamped with provenance from M0's `core`.

## The bake-off (per module — ours vs Vincent's)

| Module | Ours | Vincent's |
|---|---|---|
| snapshots | `backend/src/snapshots/{as_of,builder,quote_quality,reference_spot}.py` | `infra/snapshots/{builder,state}.py` |
| forwards | `backend/src/forwards/{estimate,parity}.py` | `infra/forwards/{engine,state}.py` |
| iv | `backend/src/iv/solver.py` | `infra/iv/{engine,solver,state}.py` |
| surfaces | `backend/src/surfaces/{svi,fit,arbitrage,reporting}.py` | `infra/surfaces/{svi,calibrate,diagnostics,engine,state}.py` |
| pricing | `backend/src/pricing/{black76,american,engine,state}.py` | `infra/pricing/{european,american,engine,config,state}.py` |
| utils | (scattered) | `infra/utils/{daycount,robust}.py` |

For each: run both test suites, compare against an independent oracle (QuantLib / py_vollib / GBSM — we already seeded constants to ~1e-14), and keep whichever implementation is more correct and better tested. Where they're equivalent, prefer the one with the cleaner frozen-dataclass contract and the stronger property tests.

## What to carry regardless of which side wins

- **Keep ours:** the `PRICER_VERSION` naming discipline (`black76-lr-1.0.0` — it is Leisen-Reimer, not CRR), the property tests (`test_pricing_properties.py`), the determinism golden + cross-process hash, and quote-QC wired into the snapshot build path (build keeps both the full and the QC-filtered `usable` view).
- **Adopt from Vincent:** explicit `state.py` per module if his frozen-state modeling is cleaner, his `surfaces/diagnostics.py`, `utils/{daycount,robust}.py`, and his golden surface fixtures (`tests/golden/fixtures/*.json`, `tests/surfaces/test_surface_end_to_end.py`).
- Land one pricer version label and one day-count source — no two implementations of the same formula surviving side by side.

## Frozen seam

Freeze the **pricing interface** early (M3 builds risk on it) — the pure pricer signature + the frozen pricing-state dataclass, stamped. Publish it so M3 and M7 bind against a stable contract, exactly as the original Workstream C froze pricing for D.

## Test surface

Read [TESTING.md] first. Specific to M2:
- Independent-oracle agreement per module (no expected value copied from code under test).
- Determinism: same inputs/version/config → byte-identical outputs, proven by golden + cross-process hash.
- Property tests: pricing monotonicities/bounds, parity for the forward, SVI no-arbitrage on the fitted surface.
- The merged surface fits the golden fixtures from both repos within tolerance.

## Done criteria

One implementation per analytics module, each the proven winner of its bake-off, pure and stamped, pricing interface frozen for M3, gate green with property + determinism + golden tests. 

## Gotchas

Resist "keep both and pick at runtime" — that is two code paths that will drift. The bake-off ends in one survivor per module. Keep the math pure: if Vincent's version reaches into I/O or a clock, that's a defect to strip, not a feature to preserve. Don't move a golden number to make a test pass — if the two implementations disagree on a number, the oracle decides which is right.
