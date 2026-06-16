# T-intraday-conid-cache — ⚡ timeliness — re-resolve nothing that is static within a session

> **⚡ Timeliness — the throughput spec's named, un-landed "optional second lever."**
> [EMERGENCY-capture-throughput](EMERGENCY-capture-throughput.md) (lines 53-55) explicitly scoped a
> second lever and it was never built:
>
> > *Optional second lever (only if concurrency alone is short): an **intraday conid cache** keyed by
> > `(underlying_conid, month) → {strike,right: conid}` — `strike→conid` is static within a session,
> > so re-runs / the constituent walk skip already-resolved discovery.*
>
> Concurrency alone *was* short (the cross-underlying march is still serial — see
> [ibkr-capture-cross-underlying-concurrency](archive/ibkr-capture-cross-underlying-concurrency.md)), so this
> lever is now in scope, not optional. It attacks the cost from the other side: the cheapest
> `/secdef/info` call is the one you never make.

## What is re-resolved today that need not be

`_qualify_contracts_concurrently`
(`packages/infra-ibkr/src/algotrading/infra_ibkr/collectors/cp_rest_close_capture.py:286-331`)
issues one `/secdef/info` round-trip per `(month, strike, right)` to map it to a conid. But within a
trading session that mapping is **static** — a strike's conid does not change between fires. Every
re-fire, retry, or re-run inside the same session re-walks the entire chain from scratch, paying the
full discovery cost again for a map it already computed minutes earlier. The constituent lane and any
intra-session re-capture are the obvious repeat offenders.

## Scope

- A **session-scoped** cache keyed by `(underlying_conid, month) → {(strike, right): conid}`,
  populated as `_qualify_contracts_concurrently` resolves contracts and consulted **before** the walk
  so a cache hit skips the `/secdef/info` call entirely. Cache miss ⇒ resolve and populate, exactly
  as today.
- **Bounded, explicit lifetime.** Keyed/scoped to the capture session (or trade date), so a *new*
  listing day re-discovers from scratch — a stale conid from a prior session must never leak into a
  new day's basket. Prefer an injected cache object (testable, no module-global surprise) over a
  process-wide singleton.
- Opt-in / observable: a typed flag if it needs one, and a structured log of hit/miss counts so the
  saving is measurable and a surprising miss rate is visible.

## Guardrails

1. **Output parity is the acceptance bar.** A capture served entirely from a warm cache must produce
   the **byte-identical** `AvailableChain` + `conid_by_contract` as a cold capture — the cache is a
   pure latency optimisation, never a behaviour change. Locked by a test that runs the same capture
   twice against a counting fake and asserts (a) identical output, (b) zero `/secdef/info` calls on
   the second run for cached months.
2. **No cross-session / cross-day bleed.** A test must prove the cache key isolates sessions: a new
   trade date (or a deliberately invalidated cache) re-resolves and does not serve yesterday's conids.
3. **Static-mapping assumption is load-bearing — state it.** The cache rests on "strike→conid is
   immutable within a session." Document it; if a mid-session re-list ever violated it, the
   per-session scope is the backstop (next session re-resolves).

## Orthogonality / seams

- Owns the discovery-resolution path in `cp_rest_close_capture.py` and the cache object's home.
  Disjoint from the snapshot phase and the quote-integrity gate. Composes cleanly with the two
  concurrency tasks — fewer calls *and* the remaining calls run concurrently.
- Lowest priority of the three throughput follow-ups: it only pays off on **re-fires / the repeated
  constituent walk**, whereas the cross-underlying and warm-up levers shorten the **first, cold**
  capture that actually races the settlement window. Land it after them, or when intra-session
  re-fires become routine.

## Done criteria

- A second capture in the same session resolves its chain from cache with **zero** `/secdef/info`
  calls for already-resolved months; output byte-identical to the cold capture (locked by test).
- Cache key isolates sessions/days (no stale-conid bleed, tested). Hit/miss observable in logs.
  Gate green.
