# T-constituent-lane-activation — ⛔ EMERGENCY — the constituent capture lane silently never fired

> **⛔ EMERGENCY.** The S1 dispersion book — the flagship strategy — trades the index's heaviest
> constituents. If their chains never land, S1 has no inputs and the unattended week captures a
> hollow universe. The 2026-06-15 canary bound scope `index+constituents` and captured **zero**
> constituents **without a single error** — the worst failure mode: silent.

> **Source:** 2026-06-15 SX5E canary (run_id `89421177611f42ff85b55ba9144f8662`, store
> `/tmp/sx5e-canary.QNKI`). Basket source logged `scope: "index+constituents"`, but the whole run
> issued **exactly one** `/iserver/secdef/search` (the index), produced **12** discovery-window
> events (all SX5E tenors), and wrote partitions for **`underlying=SX5E` only**. No constituent was
> resolved, attempted, or logged as skipped.

## What's configured vs what ran

The lane is **built and configured** — this is a wiring/precondition failure, not missing code:

- `configs/universe.yaml`: `constituent_top_n: 10`, resolved **point-in-time from 1A membership
  weights** by `top_n_by_weight` (never a hand-set list), and it "**rejects a basket with missing
  weights — you cannot rank what you do not know**" (the config's own words, `:86`).
- The collector exists: `cp_rest_constituent_capture.py`; the generic mechanics
  (`collect_target_basket`, already underlying-generic) are in place; wiring lives in
  `infra/orchestration/eod_stages.py` and `infra_ibkr/live_capture.py`.

Yet the lane no-opped. **Prime suspect:** the canary temp store had **no 1A membership weights**,
so `top_n_by_weight` returned an empty basket (correctly refusing to rank unknown weights) — and
the constituent lane then captured nothing **silently**, with no log line and no non-zero exit.
That silent-empty path is itself a bug: "no constituents resolved" must be **loud**.

## The gap (two, both EMERGENCY)

1. **Activation/precondition:** with membership weights present, scope `index+constituents` must
   actually drive the constituent collector for all N=10 names. Diagnose precisely why it didn't
   here (missing-weights precondition vs runner not iterating the generic lane vs basket-source
   binding) — confirm against the store/log, don't assume.
2. **Fail-loud on empty:** "scope says constituents but zero were resolved/attempted" must emit a
   **critical** and exit non-zero (so `OnFailure=` alerts), never a clean exit. The same silence
   that hid this must not hide it tomorrow night.

## Scope

- Trace the constituent lane from `eod_stages.py` / `live_capture.py` through
  `top_n_by_weight` → `collect_target_basket`; identify the exact precondition that left it empty in
  the canary and make scope `index+constituents` **demonstrably attempt all 10** when weights are
  present.
- Ensure 1A membership weights are loaded (or fail loud naming the missing input) **before** the
  capture stage, so the top-N resolver has something to rank — and so a weights gap is an explicit
  error, not a silent empty basket.
- **Per-name outcome ledger:** every constituent records a labelled result — `captured(n_options)`
  / `no_options` / `unentitled` / `unresolved(symbol)` — so we finally learn **which of the 10 SX5E
  constituents return option chains on this account** (the open entitlement question the canary was
  meant to answer and didn't reach). Surface it in the capture-coverage panel.
- Add a guard: scope includes constituents ⇒ at least one constituent attempted, else critical +
  non-zero exit.

## Orthogonality / seams

- Touches the **runner/orchestration + universe membership** (`eod_stages.py`, `live_capture.py`,
  `top_n_by_weight`) — **disjoint** from both
  [EMERGENCY-quote-integrity-gate](EMERGENCY-quote-integrity-gate.md) (per-row quote quality) and
  [EMERGENCY-capture-throughput](EMERGENCY-capture-throughput.md) (discovery-walk speed). Once the lane fires,
  it benefits from both but shares no edited file with them.
- Note: activating 10 constituents multiplies the walk cost ~11× — this is the dependency that makes
  [EMERGENCY-capture-throughput](EMERGENCY-capture-throughput.md) load-bearing for the *timed* close. Land
  them together for the unattended week.

## Done criteria

- A run with scope `index+constituents` and membership weights present **attempts all 10**
  constituents; each yields a labelled outcome; their partitions appear under
  `raw/.../underlying=<SYMBOL>` (or a clean, logged per-name no-capture with reason).
- "constituents in scope but none attempted/resolved" is a **critical, non-zero-exit** failure.
- The per-name chain-vs-empty entitlement verdict for the 10 SX5E names is recorded and visible.
- Gate green.
