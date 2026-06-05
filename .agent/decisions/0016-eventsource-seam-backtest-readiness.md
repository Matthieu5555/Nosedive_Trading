# 0016 — `EventSource` Protocol: backtest readiness without forking the pipeline

- **Status:** accepted. **Update 2026-06-05:** under the Nautilus runtime spine
  ([[0023-nautilus-runtime-spine-and-library-leverage]] / [[0025-nautilus-host-catalog-topology]])
  the no-fork **invariant holds** — the host replays a `RawMarketEvent` sequence through the same
  engine — but the **`EventSource` Protocol itself stays YAGNI / unimplemented** (0025 §5). So the
  `replay_range`-on-Protocol wiring described here is hypothetical; the principle, not the mechanism.
- **Date:** 2026-06-03
- **Source:** Vincent's ADR-021; merged 2026-06-05

## Context

The strategy and backtesting layers are anticipated. The blueprint imposes a single code path for
both live and replay: *"The canonical run order applies both to live and replay. The only difference
being the source of events"* (Part IV §F) and *"resist the temptation to fork a separate
historical-only implementation"* (Step 13 / Part XVIII). Our ADR 0007 already bans the dual-path
fork.

The analytics chain (`reconstruct_day`) is clock-pure and deterministic — no wall-clock, RNG, or
threading between snapshots → QC → forwards → IV → surfaces → pricing. The gap: `replay_range` was
coupled to the `RawStore` concrete class rather than to an abstraction. A future backtest provider
or historical data feed would have to either duplicate the pipeline (banned) or hack
`reconstruct_day` to accept a different source.

## Decision

1. **Extract a minimal `EventSource` Protocol** in `infra`:
   `events(provider, underlying, start, end) -> Iterable[RawMarketEvent]`. The existing
   `ReplaySource` already satisfies it. `replay_range` depends on the Protocol, not the concrete
   store.

2. **Three sources plug in at the same seam without changing the pipeline:**
   - Live: events arrive from the collector → raw layer (live capture path).
   - Replay: events read from the stored raw layer (`ReplaySource`).
   - Future historical provider: any source that normalizes to `RawMarketEvent` and satisfies
     `EventSource`.

3. **The strategy/backtest read contract is documented, not built (YAGNI):** read surfaces from
   `DerivedStore` (`read_surface`/`read_forward`/`read_snapshot`/`read_iv_points`) + call
   `pricing.price` — the only sanctioned state-to-price/Greeks module. This mirrors
   `orchestration/risk_pipeline.build_analytics`.

4. **`as_of` is the valuation clock injection point.** `reconstruct_day(as_of=...)` already exists.
   A backtest harness injects `as_of` per day; no wall-clock enters the chain.

5. **Capture-forward is the first historical backfill mechanism**, not a separate engine. The two
   existing CLIs (`capture`, `reconstruct_run`) are idempotent and partitioned by `trade_date`; they
   need only an external scheduler trigger (cron / Task Scheduler), not a new Python module.

6. **Explicitly deferred (YAGNI):** strategy signals (`packages/strategy` stays skeleton),
   fill/slippage/order simulation (`packages/execution`), walk-forward harness, PnL attribution,
   and any historical data provider implementation. The `EventSource` Protocol *is* the seam — the
   implementation waits for a real depth-of-history need.

## Consequences

The Protocol addition is ~10 lines. `replay_range` depends on it → a backtest or historical
provider is a new source, zero pipeline change. The single-path invariant (ADR 0007) is maintained.
Exit cost: low — additive Protocol; revert by calling `RawStore` directly.
