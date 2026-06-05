# 0009 — Surface job, broker-agnostic chain planning, and market-data policy

- **Status:** accepted
- **Date:** 2026-06-05

## Context

`backend/scripts/vol_surface.py` proved the live→surface pipeline end to end (ADR 0008),
but it was the *first place the whole workflow existed*: live acquisition, chain selection,
the actor invocation, the ATM-vol math, and terminal rendering all lived in one CLI script,
and broker-agnostic policy (which contracts to request; whether the feed is even entitled)
was embedded in the IBKR adapter and the script. A second consumer — a scheduled job, a
replay, an API, a notebook — would have had to copy that logic. The live run also surfaced
a real blocker (no OPRA option entitlement) only as broker log spam an operator had to
interpret. This ADR records where each of those decisions now lives, so the seams are not
re-litigated.

These are pure information-hiding moves: no analytics changed, and the byte-identical
replay and provenance tests are unmoved (they are the regression guard).

## Decision

1. **Chain-selection policy lives in `universe.chain_planning`, not the IBKR adapter.**
   Deciding *which* slice of an option chain to qualify — the nearest N expiries, a strike
   window around spot, a minimum per side, and which listing to expand (primary trading
   class before a secondary settlement class — the SPY/2SPY rule) — is broker-agnostic. It
   now operates on a broker-neutral `AvailableChain` and returns a `ChainPlan`
   (`ChainSelection`, `select_chain`/`select_expiries`/`select_strikes`, `plan_chain`). The
   IBKR adapter keeps only the one broker-specific step: normalizing `reqSecDefOptParams`
   rows into `AvailableChain`, then expanding the returned plan into real contracts.
   `universe` already owns "what instrument is this?"; defining the tradable slice is the
   same concern. `connectivity` does not import `universe` elsewhere, so this introduces no
   cycle. `ChainSelection`'s home moved from `connectivity` to `universe`; it is no longer
   re-exported from `connectivity` (callers import it from `universe`).

2. **The feed-notice vocabulary moved down into `connectivity.market_data_policy`;
   `collectors` re-exports it.** `FeedNotice` / `classify_feed_notice` and the pacing/
   entitlement code sets used to live in `collectors.notices`. To let the broker adapter
   classify its *own* error events (the proposal's "classify 10091 into structured
   diagnostics"), the classifier must be reachable from `connectivity` — but `connectivity`
   cannot import `collectors` (that is a cycle: `collectors` imports `connectivity`). So the
   vocabulary moved *down* to `connectivity`, where broker notices belong, and
   `collectors.notices` became a thin re-export so existing collector code and tests are
   unchanged. The entitlement code set gained `10089`/`10091` (the delayed-data /
   not-subscribed downgrade notices the OPRA-less live run actually received).

3. **Requested vs effective market-data capability is a value: `MarketDataStatus`.** A live
   feed can accept every subscription and still produce nothing. `assess_market_data` pairs
   what was *requested* against what was *effective* (read off the ticks) against how many
   subscriptions actually *produced*, plus the classified notices, and `describe()` names
   the likely cause. The IBKR adapter captures the inputs clock-free: `feed_errors()` keeps
   raw `(code, message)` notices and `observed_market_data_type` is read off the ticks; a
   caller with a clock classifies them. This keeps the adapter consistent with the rest of
   its paths (nothing in it reads a clock) and makes "no data, here's why" a structured
   result rather than log spam.

4. **The reusable use case is `orchestration.build_surface`; the CLI is a thin client.**
   `build_surface` composes the existing jobs — resolve+materialize the chain,
   `collect_live`, `assess_market_data`, `run_incremental_analytics` (empty book), and
   `surfaces.summarize_surface_parameters` — and returns a `SurfaceJobResult`. It is
   broker-agnostic (drives any `BrokerSession` via the supervisor) and takes entitlement
   diagnostics from an *optional* `MarketDataDiagnostics` source, so a fake/replay session
   works and only the live adapter contributes entitlement detail. For a live run the
   request's `as_of`/`calc_ts` are left unset and stamped from the clock *after* collection,
   so a snapshot never values as-of a time before the quotes it read (no look-ahead);
   tests and replay pass them explicitly for reproducibility. `scripts/vol_surface.py` is
   now argument parsing, dependency construction, the job call, and rendering — nothing else.

5. **The fit→contract projection rule lives in `surfaces.project_surface_fit`.** "SVI emits
   parameters and a grid, a nonparametric fallback emits a grid only, an insufficient slice
   emits nothing" was encoded inside `actor.driver._build_surfaces` (where a latent bug had
   lived). It moved next to the method semantics in `surfaces`, returning a
   `SurfaceProjection`; the actor now persists whatever the projection yields rather than
   re-encoding the rule.

## Consequences

- The CLI, a future scheduler, a replay job, and an API all reach a surface through one
  path (`build_surface`) instead of copying live-collection logic.
- Installing the `ibkr` extra and running `uv run mypy .` is now green; the three
  pre-existing `ibkr_session.py` type errors were fixed as a by-product of the adapter
  rewrite in decision 1.
- `ChainSelection` imported from `connectivity` now fails; import it from `universe`. Two
  in-repo scripts were updated; no other callers existed.
- Feed-notice imports via `collectors` are unchanged (re-export), so no collector or QC
  code moved.
