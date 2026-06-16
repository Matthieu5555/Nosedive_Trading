# T-capture-cross-underlying-concurrency — ⛔ EMERGENCY/timeliness — the 11 underlyings still run strictly serial

> **⛔ EMERGENCY (timeliness) — the unfinished half of [EMERGENCY-capture-throughput](EMERGENCY-capture-throughput.md).**
> That spec landed only its *first* lever: a bounded pool on the `/secdef/info` walk **inside one
> underlying** (`discovery_pool_size`, commit `7697e77`). Its load-bearing done-criterion —
> *"wall-clock for the index + 10 constituents fits comfortably inside the post-close settlement
> window with margin"* — was **never met and never demonstrated**. The constituent lane fires the
> index and each of the 10 constituents **strictly one after another**, so the ~7-min index-only
> walk is still multiplied ~11× at the outer loop. Concurrency *within* a single underlying does
> nothing for the serial march *across* underlyings.

> **Source:** code, not a fresh canary. `collect_index_and_constituents_basket`
> (`packages/infra-ibkr/src/algotrading/infra_ibkr/collectors/cp_rest_constituent_capture.py:442-455`)
> resolves the top-N then captures each name in a serial list comprehension:
>
> ```python
> results = [
>     _attempt_constituent(transport, member=member, rank=rank, target=..., ...)
>     for rank, member in enumerate(top_n, start=1)
> ]
> ```
>
> The index spine (`collect_live_basket`) runs first, then this loop, end-to-end, one underlying at
> a time. Each `_attempt_constituent` is a full capture (spot snapshot → per-month strikes →
> concurrent info walk → snapshot warm-up), so 11 underlyings ⇒ 11 serial captures.

## The bottleneck (latency-bound, across underlyings)

Every constituent capture is **independent** of the others — different conid, different chain,
different snapshot. They are gated only by the single paced CP Gateway session, not by each other.
Running them serially leaves the CPU idle while each name's network round-trips drain one at a
time. This is the same latency-vs-compute argument the discovery-walk lever already won, applied
one level up: the *outer* loop over underlyings.

## Why concurrency is safe here (output is provably order-independent)

- Each underlying's basket is assembled independently and merged by `_merge_baskets`
  (`cp_rest_constituent_capture.py:336-354`), which **concatenates** instruments/events/masters.
  Event ids are **content-addressed** (the close-capture assigns `sequence` from the contract's
  canonical key, not arrival order), so a shuffled completion order yields byte-identical events —
  the merge introduces no order sensitivity (its own docstring says so).
- The per-name outcome ledger already isolates each name's verdict
  (`captured` / `no_options` / `unentitled` / `unresolved`); recording it from a worker thread is a
  list append, order-independent (sort the ledger rows by rank before persisting for a stable
  golden).
- The index spine keeps its privileged status: its failure still fails the fire; a constituent's
  failure stays a *recorded outcome*, exactly as today.

## ⚠️ The one real hazard: do NOT nest pools

The single paced CP Gateway session is the binding constraint. Naively wrapping the constituent
loop in its own pool **multiplies** with the existing per-walk pool: `discovery_pool_size` (6) ×
N concurrent underlyings ⇒ dozens of simultaneous in-flight calls, which the gateway answers with
a 429 storm that the backoff serialises back to ~sequential — a **net loss**, the exact failure the
throughput spec warned against.

The correct design is **one shared, capture-wide concurrency budget** (a single bounded semaphore /
executor threaded through the whole capture), against which *both* the cross-underlying fan-out and
the within-underlying `/secdef/info` walk draw. Total in-flight gateway calls stay bounded by one
number regardless of how the work is decomposed. The existing `discovery_pool_size` either becomes
that single shared budget, or is explicitly subordinated to it — never multiplied by it.

## Scope

- Thread a **single shared bounded executor / semaphore** through `collect_index_and_constituents_basket`
  so the index + N constituents capture concurrently, and have `_qualify_contracts_concurrently`
  draw from that **same** budget instead of opening its own independent pool. Net concurrency
  against the gateway is one typed knob, not a product.
- Typed config (ADR 0028, no `.py` literal): a capture-wide pool width (e.g.
  `universe.strike_selection.capture_pool_size` or a sibling), conservative default; document that it
  is the *total* gateway concurrency, and that `discovery_pool_size` now composes into it.
- Keep the transport's 429/503 backoff as the pacing valve; surface a structured log of total
  in-flight / retries so over-pacing is observable. A pool of 1 reproduces today's serial walk.
- The index spine stays load-bearing (its failure fails the fire); constituent failures stay
  per-name recorded outcomes. The ledger is written once, after the fan-out joins, rows sorted by
  rank for a stable golden.

## Guardrails

1. **Output parity is the acceptance bar.** A fixture-driven test must assert the concurrent
   cross-underlying capture produces the **identical** merged `IndexBasket` (instruments, events,
   masters) **and** the identical `constituent_capture_outcomes` ledger as the serial capture. Not
   identical ⇒ wrong.
2. **One bounded budget, proven before raised.** Demonstrate the gateway tolerates the chosen total
   concurrency (429 rate observable in logs, no rise in dropped/unresolved contracts) before
   widening it. A flood the backoff serialises back is a regression.
3. **Do not touch what is captured** — not the strike window (owner ruling), not the quote-integrity
   gate, not the per-name outcome semantics. The win is "the same baskets, sooner," never "fewer or
   thinner baskets."

## Orthogonality / seams

- Owns the **outer orchestration** in `cp_rest_constituent_capture.py` and the shared-budget plumbing
  into `cp_rest_close_capture.py::_qualify_contracts_concurrently`. Disjoint from the quote-integrity
  gate (per-row quality) and the constituent-lane *activation* (which names fire) — both already
  landed; this changes only **how fast** the already-firing lane completes.
- Composes with [ibkr-snapshot-warmup-concurrency](ibkr-snapshot-warmup-concurrency.md) (which
  removes the *per-batch* serial warm-up inside each underlying) and
  [ibkr-intraday-conid-cache](ibkr-intraday-conid-cache.md) (which lets a re-fire skip discovery).
  All three draw from the same capture-wide budget — land the budget here first, the others plug in.

## Done criteria

- Index + 10 constituents capture concurrently under **one** bounded gateway-concurrency budget;
  wall-clock fits comfortably inside the post-close settlement window **with margin**, and that
  margin is measured and recorded (the throughput spec's unmet criterion, finally demonstrated).
- Merged basket + per-name ledger are **byte-identical** to the serial capture on a fixture (locked
  by test). Total gateway concurrency is one typed knob; 429 behaviour is observable; no increase in
  dropped/unresolved contracts. Gate green.
