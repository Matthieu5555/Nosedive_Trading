# 1C — Capture: daily close snapshot + IBKR historical OHLC backfill

> **Phase 1, roadmap WS 1C.** Two capture paths the platform does not yet have: a
> **daily close-snapshot mode** (one immutable, provenance-stamped snapshot set per day for the
> index + every name) and an **IBKR historical-bar fetch** (years of underlying daily OHLC per
> ticker). The live/recent actor capture exists and is tested; neither of these does. The history
> fetch follows **[ADR 0031](../.agent/decisions/0031-ibkr-historical-data-cp-rest-oauth1a.md)**
> (Client Portal REST `/iserver/marketdata/history`, OAuth 1.0a). Blueprint **[ADR 0011](../.agent/decisions/0011-blueprint-as-plan-of-record.md)** overrides on every domain question below.

- **Owns:** a daily close-snapshot capture mode on the actor/orchestration path
  (`packages/infra/src/algotrading/infra/actor/driver.py`,
  `packages/infra/src/algotrading/infra/orchestration/jobs.py`), the new
  `DailyBar` table (P0 contract) in `packages/infra/src/algotrading/infra/contracts/{tables,registry}.py`,
  and the IBKR historical-bar fetch extending the CP REST transport
  (`packages/infra-ibkr/src/algotrading/infra_ibkr/connectivity/{cp_rest_transport,cp_rest_session}.py`
  plus a new OAuth 1.0a module + a history collector beside
  `collectors/cp_rest_adapter.py`). Storage per
  **[ADR 0019](../.agent/decisions/0019-one-immutable-raw-model.md)** /
  **[ADR 0033](../.agent/decisions/0033-analytical-storage-duckdb-polars-over-parquet.md)**;
  partition layout per **[ADR 0034](../.agent/decisions/0034-data-retention-compaction-and-backend-disposition.md) §4**.
- **Depends on:** **P0** (the `DailyBar` contract + the option `MarketStateSnapshot` shape, OQ-2);
  **1A** (the point-in-time basket to capture); **1B** (the delta-band selection of which contracts
  enter the close set); **[D1](D1-storage-foundation.md)** — **the `provider` partition segment MUST
  land before equity (SX5E / SP500) capture at scale.** Crypto-only writes (DERIBIT, where
  `provider == exchange`) can proceed without D1, but no equity bar/snapshot may be written to disk
  until D1's `provider=<P>/…` layout exists, or two sources of the same symbol mix on disk (the exact
  failure 0017/D1 prevent). ADR 0031 is accepted.
- **Blocks:** **1F** (projection reads the close snapshots + daily bars), **1G** (the cron job that
  fires this capture unattended each day).
- **State going in (audited 2026-06-07):** the actor (`run_analytics` / `run_day`) and the collectors
  (`RawCollector`, live == replay one code path) exist and are tested
  (`test_replay_byte_identical.py`, `test_collectors.py`, `test_seam_analytics.py`).
  `orchestration/pipeline.py::run_end_of_day()` is a one-shot, idempotent, restartable EOD sequence;
  `jobs.py` holds `collect_live` + `run_incremental_analytics`. **No daily close-snapshot mode**
  exists — capture is live/recent only. **No underlying-bar capture** and **no `DailyBar` table**
  (`contracts/tables.py` has option/derived contracts only — verified). **No historical fetch in the
  IBKR adapter**: `cp_rest_adapter.py` is read-only `/iserver/marketdata/*` (snapshot + WS) per
  ADR 0024 §4; `cp_rest_session.py` does `/iserver/auth/status` + `/tickle` only; `cp_rest_transport.py`
  is a bare GET/POST with **no OAuth, no history endpoint**. (The roadmap table still names
  `store_serving.py` shipping `stock_snapshots=[]`; **that file was deleted in C4** — do not cite it.
  The real capture path is the actor driver + the orchestration jobs above.)

## Objective

A single daily run writes **one immutable, provenance-stamped close-snapshot set** covering the index
and every constituent (the option `MarketStateSnapshot` rows the rest of Phase 1 builds on), and a
**backfill run populates years of daily OHLC bars per ticker** into a new `DailyBar` table — both
landing in the immutable Parquet record, partitioned per ADR 0034 §4, replayable and re-readable. The
close set is point-in-time honest: it captures the session's close, never a future value. The history
fetch is unattended-capable over CP REST + OAuth 1.0a, with no TWS / IB Gateway and no Nautilus
historical client.

## What to do (ordered)

### Part A — `DailyBar` contract + storage registration

1. Land the **`DailyBar`** contract (defined in P0): `(provider, underlying, trade_date, open, high,
   low, close, volume, …)` + a `ProvenanceStamp`, **distinct** from the option `MarketStateSnapshot`
   (OHLC, not a quote snapshot — it is what makes the candlestick chart free for 1I). Register it in
   `contracts/registry.py` (`REGISTRY` `TableSpec`) and `contracts/tables.py`, and route it in
   `table_for_contract`. Mark it **provider-partitioned** in the registry (D1's classification): a bar
   carries the source it came from.
2. Confirm it round-trips through `ParquetStore.write`/read (write-ahead validation, A's schema), and
   that a malformed bar (negative volume, `high < low`, `open`/`close` outside `[low, high]`, NaN) is
   **rejected with an explicit error**, not silently coerced.

### Part B — daily close-snapshot capture mode on the actor

3. Add a **close-snapshot mode** that produces one snapshot set for a trade date from the session's
   close. Reuse the existing pure path: `build_snapshots` already accepts `session_open` and
   `reference_spot.py` already resolves a `close` reference type (`prior_close`, look-ahead-guarded —
   `reference_spot.py` line 74 spells out the contract). The mode is "session closed, reference =
   close", an injected `as_of` = the close instant; it must **not** read a wall clock (replay
   byte-identical depends on injected `as_of`/`calc_ts` — see the driver module docstring).
4. The close set covers the **1A basket** at the **1B-selected** contracts (index + every constituent).
   Emit exactly one immutable set per `(provider, trade_date)`; re-running the same day replaces those
   derived partitions and never touches the append-only raw layer (driver `persist_outputs` is already
   replace-/append-idempotent). Wire it as a callable the EOD `collection`/`analytics` stage can invoke
   (1G owns the schedule; 1C only provides the mode).

### Part C — IBKR historical-bar fetch (ADR 0031)

5. **OAuth 1.0a in-house** (Live Session Token, ~24h): a new signing module in `packages/infra-ibkr`
   using **pycryptodome** (new dependency; reference `ibind`, do not add it as a dep). No daily
   interactive login, no 2FA. Add nothing economic as a `.py` literal — hosts/URLs/timeouts/retry
   parameters come from validated config (the C7 no-hardcode discipline).
6. **Extend `cp_rest_transport` / `cp_rest_session`**, do not fork them: sign requests with the LST;
   keep the session alive with the tickler (`cp_rest_session.py` already owns the `/tickle` loop);
   open/maintain the brokerage session (`ssodh/init`) and **wait for `established:true`** before any
   history request.
7. **Fetch daily OHLC** via `GET /iserver/marketdata/history` (`bar=1d`, `period` up to the years the
   roadmap needs), one new history collector beside `cp_rest_adapter.py` that normalizes a bar row into
   a `DailyBar` (mirror the `cp_rest_normalize` snapshot→event pattern). Keep the **read-only invariant**
   (ADR 0024 §4 extended by 0031 §Consequences) — only `/iserver/marketdata/*`, never an order endpoint.
8. **Unattended hardening:** use a **dedicated second IBKR username** for the backfill (one username =
   one brokerage session; sharing it knocks out the live-snapshot feed); honour the **5-concurrent-request
   cap** and the history **warmup call**; **retry/backoff** around IBKR maintenance windows and
   schedule off-window. Make the fetch resumable — a backfill killed mid-run re-fetches only the
   missing `(ticker, date-range)` tail (idempotent on `(provider, underlying, trade_date)`).
9. **As-of / no look-ahead:** a backfill for ticker T writes one bar per past trade date with that
   date's own OHLC; nothing stamps a bar with data from after its `trade_date`. Run the
   `check-lookahead-bias` skill over the fetch + normalize path before declaring it done.

## Test surface

Read [TESTING.md](TESTING.md). Independent oracles, expected values derived independently of the code
under test, named cases (not outcome prose), edge cases mandatory. Specific:

- **`DailyBar` contract round-trip** (extends `test_storage_port.py` / `test_storage.py`): a bar writes
  and reads back **equal**; a malformed bar (`high < low`, `close` outside `[low, high]`, negative
  volume, NaN) is **rejected with an explicit error** — at least one malformed instance per the
  TESTING.md seam rule. An old partition lacking a later-added nullable column still reads
  (additive-nullable, per D1).
- **Provider isolation** (D1 seam): a `DailyBar` for the **same `(underlying, trade_date)` from two
  providers** lands in **disjoint** partitions and never mixes; a `read` without a `provider` filter
  does not silently merge them. Gate this test behind D1 having landed.
- **Close-snapshot determinism**: feeding the same close events twice yields a **byte-identical**
  snapshot set (extends `test_replay_byte_identical.py` / `test_nautilus_replay_byte_identical.py`);
  **reordering** the input close events leaves the persisted set unchanged (reordering-invariance per
  TESTING.md). One day's run writes **exactly one** set per `(provider, trade_date)`; a second run
  replaces, does not duplicate.
- **No look-ahead in the close reference**: a test feeds a `prior_close` and a (would-be) future close
  and asserts the snapshot uses the prior, never the future (the `reference_spot.py` look-ahead
  contract); `check-lookahead-bias` passes over Part B and Part C.
- **OAuth 1.0a signing**: the LST signature for a fixed `(consumer key, nonce, timestamp, base string)`
  matches a **hand-computed / ibind-reference** expected signature (independent oracle — a known-answer
  vector, not the signer checked against itself). A bad/expired token raises a **labeled** auth error,
  not a bare exception.
- **History normalize → `DailyBar`**: a captured/sample IBKR `marketdata/history` payload (a real
  sample, like `test_real_sample_reconstruct.py` does for snapshots) normalizes to the **expected**
  `DailyBar` rows — OHLC values, `trade_date`, and `bar=1d` mapping all asserted against the raw payload
  read by hand. An empty window and a single-bar window are both exercised.
- **Read-only invariant** (mirror `test_cp_rest_adapter.py`'s read-only assertion): the history path
  touches only `/iserver/marketdata/*` + auth/tickle, never an order endpoint.
- **Session gating**: a history request issued before `established:true` is **deferred/raised**, not
  sent; the tickler keepalive and the established-wait are exercised with an injected clock (no real
  sleep), as `test_cp_rest_session.py` does.
- **Backfill resume**: a backfill killed after K of N tickers, restarted, re-fetches **only** the
  missing tail and the final on-disk set is identical to an uninterrupted run.
- **Edge cases (floor)**: empty basket, a ticker with no history in the window, a maturity/date exactly
  on the session boundary, a `provider` that is empty/multi-segment (rejected per D1's validation).
- **Gate green**: `ruff && mypy && lint-imports && pytest`. uv only for every command.

## Done criteria

A single daily run writes one immutable, provenance-stamped close-snapshot set for the index + every
constituent, exactly one per `(provider, trade_date)`, replace-idempotent on re-run. A backfill run
populates years of daily OHLC into the new provider-partitioned `DailyBar` table, one bar per past
trade date per ticker, no look-ahead, resumable. The IBKR history fetch runs unattended over CP REST +
in-house OAuth 1.0a (pycryptodome), waits for `established:true`, honours the 5-request cap + warmup,
retries around maintenance windows, and stays strictly read-only — no TWS/IB Gateway, no Nautilus
historical client. Equity capture at scale is gated on D1's `provider` segment having landed. Every
case above has a named test with an independent oracle; root gate green.

## Gotchas

- **Do not cite `store_serving.py`** — C4 deleted it. The capture path is the actor driver
  (`run_analytics` / `run_day`) + `orchestration/jobs.py`; the close-snapshot mode lives there.
- **D1 is a hard prerequisite for equity scale.** Writing SX5E/SP500 bars before the `provider`
  partition segment lands mixes ASML-from-SAXO with ASML-from-IBKR on disk and corrupts every
  surface/backtest. Crypto-only (DERIBIT) is the only thing safe to write before D1.
- **Dedicated second IBKR username** for the backfill is not optional — one username is one brokerage
  session across all IBKR platforms, so reusing the live-feed username silently knocks the live feed
  offline (ADR 0031 §4).
- **Never read a wall clock** in the close-snapshot mode — inject `as_of` / `calc_ts`, or the
  byte-identical replay invariant breaks (the property four other specs depend on).
- **OAuth 1.0a on individual accounts is slightly off IBKR's happy path** (support sometimes denies it
  though it works in practice); maintenance windows cause unavoidable outages — hence retry/backoff +
  off-window scheduling, and **IBeam (Dockerised CP Gateway + TOTP)** is the documented fallback if
  registration proves unworkable (ADR 0031 §6).
- `DailyBar` (OHLC price history) is **not** the option `MarketStateSnapshot` (a quote snapshot) —
  separate tables, separate purposes; do not overload one for the other.
- `/hmds/history` is **deprecated** — use `/iserver/marketdata/history` (ADR 0031 §Context).
- uv only — no bare `pip`/`python`; new dep `pycryptodome` added via uv.
