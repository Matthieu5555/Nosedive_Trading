# 0008 — Live IBKR adapter: two-phase universe expansion, optional SDK, read-only

- **Status:** accepted; **the `ib_async` `IbkrBrokerSession` is superseded by
  [[0023-nautilus-runtime-spine-and-library-leverage]]** (2026-06-05) — IBKR connectivity moves to
  Nautilus's shipped adapter. The decisions here (bounded `ChainSelection`, read-only, two-phase
  universe expansion) remain the reference for what the Nautilus-fed IBKR path must reproduce.
- **Date:** 2026-06-05

## Context

`connectivity` was designed with one live broker session deliberately left unwritten:
the seam, the backoff supervisor, the fake, and the disk replay all existed, but the
"gotcha" in `connectivity/README.md` said no concrete `IbkrBrokerSession` was vendored
(ADR 0003). This ADR records the choices made when that adapter was finally written —
`backend/src/connectivity/ibkr_session.py`, over `ib_async`, verified against a paper
Gateway. They are not obvious from the code and would otherwise be re-litigated.

The driving goal is a volatility surface for "any stock." That requires turning a bare
symbol into a complete, `conId`-keyed option universe the existing analytics pipeline
(snapshots → forwards → IV → surfaces → risk) can consume unchanged. The adapter's job
is exactly that and no more: supply observations; it does not compute analytics.

## Decision

1. **`request_option_chain(symbol)` returns resolved per-contract rows, not IBKR's
   sec-def parameter grid.** IBKR's `reqSecDefOptParams` returns a *menu* — one row per
   listing exchange carrying that exchange's full lists of expirations and strikes. That
   is not an instrument universe; it is the input to building one. The universe seam
   (`universe.resolve_chain` / `materialize_universe`) consumes *resolved* rows: one plain
   mapping per tradable contract with `conId`, `symbol`, `secType`, `exchange`,
   `currency`, `multiplier`, and (for options) `expiry`, `strike`, `right`. So the adapter
   does the full expansion — qualify the underlying, read its chain parameters, build
   `Option` contracts for the selected expiries/strikes, and qualify each to its own
   `conId` — and emits resolved rows. A caller never has to know how IBKR chain discovery
   works. Returning the raw grid would leak an IBKR-specific intermediate shape across the
   seam and force every caller to re-implement the expansion.

   The raw grid is still reachable for diagnostics through `option_chain_parameters`,
   which is **not** a `BrokerSession` Protocol method — it is an adapter-only escape hatch
   for inspecting what the gateway offered before selection.

2. **The chain is bounded by a `ChainSelection`, centered on a spot snapshot with a
   deterministic fallback.** "Any stock" cannot mean "every listed expiry and strike": a
   full OCC chain is thousands of contracts, trips IBKR pacing, and makes an unusably
   sparse surface. `ChainSelection` keeps the nearest `max_expiries` maturities and the
   strikes within `± strike_window_pct` of spot, but always at least
   `min_strikes_per_side` either side so a wide strike ladder still yields enough points
   to fit. Spot comes from a one-shot `reqTickers` snapshot. A missing snapshot must
   *widen* the selection, never abort the universe build, so a failed/empty snapshot falls
   back to a symmetric block around the median listed strike — bounded and deterministic,
   just not centered on the true forward. The defaults (`max_expiries=8`,
   `strike_window_pct=0.35`, `min_strikes_per_side=10`) target enough maturities and
   near-the-money strikes for a slice without pulling the whole chain.

3. **`ib_async` is an optional extra; the SDK, its tick-type enum, and its chain-discovery
   shape all stay inside the adapter.** It is declared as `[project.optional-dependencies]
   ibkr` and imported *lazily inside the methods that talk to the gateway*, so importing
   the module or the whole package never requires the SDK. This is what keeps the quality
   gate, the seam tests, and the disk replay running broker-free (`uv run pytest` with no
   `ib_async` installed), and it preserves the seam's trust boundary: the native
   integer tick-type enum is mapped to the plain `BrokerTick.field_name` string here, and
   the sec-def grid is expanded here, so neither crosses the boundary (ADR 0003).

4. **Read-only, and `StartupFetchNONE` to skip the positions/orders startup fetch.** The
   session connects `readonly=True` — no order endpoint is ever called, upholding the
   platform-wide "places no orders" invariant (`known-limitations.md`). On connect it
   passes `fetchFields=StartupFetchNONE` to skip the positions/orders/account startup
   queries, which are useless for a data feed and which a read-only login lets time out,
   stalling connect. (`StartupFetchNONE` is `ib_async`'s module-level zero value of the
   `StartupFetch` flag; an earlier draft used a non-existent `StartupFetch.NOTHING` member
   that would have raised `AttributeError` at connect.)

5. **Tested against a hand-built fake `ib_async`; the live socket is proven by a smoke
   script, not the suite.** The spec bans a live IBKR session in the test suite (ADR
   0003), so `tests/test_ibkr_session.py` installs a fake `ib_async` module and drives the
   SDK paths through it. The headline assertion is end-to-end: the rows the adapter emits
   are *accepted by the real universe resolver*, and stream through the real
   `SessionSupervisor` and `MarketDataCollector` all the way to persisted
   `RawMarketEvent`s. The live proof — a real socket to a running Gateway — is
   `scripts/ibkr_live_smoke.py` (`uv sync --extra ibkr` first), which connects read-only,
   expands a bounded chain, subscribes, collects for a fixed window, and asserts at least
   one raw event was written and no order was placed.

## Consequences

- The start-of-day runbook's universe-refresh and collection steps now have a real
  broker behind them, not only the fake; the snippet there is unchanged because the seam
  is unchanged — `IbkrBrokerSession` is just another `BrokerSession`.
- The "no live IBKR adapter is vendored" gotcha in `connectivity/README.md` and the
  matching item in `known-limitations.md` are superseded by this ADR and updated in place.
- Surfaces for a new symbol are now gated only on feeding the existing engine a correct,
  complete, `conId`-keyed option universe and quote stream — which this adapter supplies.
  Scenario shocks remain where they were (`risk/scenario.py`, ADR 0006): the adapter
  supplies observations, the actor builds market state, risk simulates shocked states.
