# T-snapshot-warmup-concurrency — ⚡ timeliness — the snapshot warm-up is paid serially, batch by batch

> **⚡ Timeliness — the second un-landed throughput lever.** The discovery walk
> ([EMERGENCY-capture-throughput](EMERGENCY-capture-throughput.md)) was made concurrent, but the
> *snapshot* phase that follows it — the actual close-mark capture — still runs its URI-safe
> batches **strictly sequentially**, and each batch pays its own cold-warm-up latency. For a full
> basket (hundreds of contracts → many 50-conid batches) this serial warm-up march is a large,
> avoidable slice of the per-underlying wall-clock, multiplied across every underlying.

> **Source:** `snapshot_with_warmup`
> (`packages/infra-ibkr/src/algotrading/infra_ibkr/collectors/cp_rest_snapshot.py:84-101`) batches
> the conids and walks them in a serial `for` loop; each batch is independently warm-up polled by
> `_warmup_poll_batch` up to `_WARMUP_ATTEMPTS = 8` times with `_WARMUP_SLEEP_S = 1.0` s sleeps
> (`:36-37`, `:53-81`). A basket of M batches that each need a cold warm-up therefore pays up to
> `M × 8 × 1 s` of mostly-idle wall-clock, end to end. Separately, `snapshot_index_spot` (`:104-121`)
> pays its **own** serial warm-up per underlying before discovery even starts.

## The bottleneck (idle warm-up latency, serialised)

The 1 s sleeps exist because a freshly-subscribed conid's market-data line is **cold** — the first
call returns metadata only, values populate a second or two later. That latency is real, but it is
*per market-data line*, not *per batch in series*: if all batches were subscribed up front, their
lines would warm **in parallel**, and the capture would pay roughly **one** warm-up window for the
whole basket instead of one per batch. Today the design pays it M times because batch *k+1* is not
even requested until batch *k* has fully converged.

## Why this is safe (output is order-independent)

`snapshot_with_warmup` already de-duplicates and **sorts** the conids, batches them, and
concatenates the rows; the caller (`_snapshot_events`) keys every row by conid and assigns
`sequence` from the contract's canonical key, never arrival order. So the batches may be warmed and
fetched in any order / concurrently and the concatenated row set — and every downstream event id —
is byte-identical. The convergence logic (stop when the populated set stops growing) is per-batch
and unaffected by *when* each batch runs.

## Scope

Two composable levers; pick the simplest that meets the window, in this order:

1. **Prime-then-poll (cheapest, no extra concurrency).** Issue the first ("subscribe") snapshot
   request for **every** batch up front so all market-data lines start warming together, *then* poll
   for convergence. The wall-clock collapses from `M × warm-up` to ≈ `1 × warm-up + M × cheap reads`.
   Keep the existing `_WARMUP_ATTEMPTS` / convergence early-exit semantics.
2. **Run batches through the shared capture-wide pool** (the bounded budget introduced by
   [ibkr-capture-cross-underlying-concurrency](ibkr-capture-cross-underlying-concurrency.md)) so
   independent batches' polls overlap under the same total-concurrency cap — no new unbounded pool.

Also fold the per-underlying **spot warm-up** into the same scheme where it overlaps cleanly (it is
a single-conid snapshot today; at minimum it can share the pool rather than block discovery start).

`_WARMUP_ATTEMPTS` / `_WARMUP_SLEEP_S` should move to typed config (ADR 0028) so the warm-up budget
is tunable against the real gateway without a code change, rather than the two module literals.

## Guardrails

1. **Output parity is the acceptance bar.** A fixture/fake-gateway test must assert the concurrent /
   primed warm-up returns the **identical** sorted, concatenated `SnapshotRow` set as the serial
   version, including the cold→warm transition (a sentinel-only first response that populates on a
   later poll). The injectable `sleep` keeps the test free of real waits.
2. **Respect the single paced session.** Priming all batches at once is more simultaneous requests —
   draw from the shared bounded budget and keep the 429/503 backoff as the valve; do not let priming
   become an unbounded burst.
3. **Convergence semantics unchanged.** An illiquid contract that never prints must still bound out
   at `_WARMUP_ATTEMPTS` and never hang the fire; "converged — dead wings won't print" early-exit
   stays.

## Orthogonality / seams

- Owns `cp_rest_snapshot.py` (the snapshot engine, shared by the close capture **and** the live
  adapter — so this speeds both). Disjoint from the discovery walk and the quote-integrity gate.
- Plugs into the shared concurrency budget from
  [ibkr-capture-cross-underlying-concurrency](ibkr-capture-cross-underlying-concurrency.md); if that
  lands first, lever 2 here is a thin wiring change. Lever 1 (prime-then-poll) stands alone with no
  dependency.

## Done criteria

- A multi-batch basket warms in ≈ one warm-up window, not `n_batches ×` one — measured.
- Snapshot row set byte-identical to the serial warm-up on a fixture, cold→warm transition included
  (locked by test). Warm-up budget is typed config. Shares the capture-wide concurrency cap; 429
  behaviour observable; no hang on never-printing contracts. Gate green.
