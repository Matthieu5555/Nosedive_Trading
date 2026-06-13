# Task — OHLC backfill for index constituents (per-component candlesticks)

**Status:** queued (deferred 2026-06-10). **Owner:** unclaimed.
**Goal:** populate `daily_bar` for the **constituents** of SPX + SX5E so Tab-1's per-component
candlestick (and the constituents list's `latest_close`) renders for the bulk of names — not just
the index underlyings.

## Current state (2026-06-10)

- **Index underlyings**: `daily_bar` present (SPX 499 bars, SX5E 504) — index candlesticks work.
- **Constituents**: only a subset of **US** names have bars (e.g. AAPL, AMZN); **NVDA = 0**, and
  **every SX5E EU name = 0** (ASML, SIE, TTE…). So per-component candlesticks are mostly empty.
- Membership + weights are seeded (SSGA SPDR, see [[index-weights-ssga-mvp]] / `configs/index_weights/`),
  so the *list* and *ordering* work; only the price history is missing.

## Root-cause diagnosis (the load-bearing finding)

`scripts/ohlc_backfill.py` is **wired correctly and honours `IBKR_CP_GATEWAY=1`**, and the Gateway
auth is fine — but over the **attended Gateway** it is impractically slow:

- The collector's **`conid=0` warmup** (`cp_rest_history.py:warmup`, "wake the data farm") returns
  **HTTP 503 in ~10 s**, and IBKR's history data-farm throws **transient 503s** per request, so each
  ticker pays retries/backoff. Observed: **~3 names / 10 min**; even `--no-constituents` (1 index)
  did not finish in 90 s. 50 (SX5E) → ~hours; 504 (SPX) → impractical; risk of stalling on one name.
- **It is NOT auth/data/our code.** The raw Gateway history endpoint is **fast** for real conids —
  verified live via `curl -k`: `conid=265598` (AAPL) and `conid=4356500` (ESTX50, EUREX) both
  returned OHLC in <25 s. The slowness is the warmup + 503-handling over the attended path.

## Constraints / gotchas

- CP REST caps a single history request at **~999 daily bars (~4 y)**, no pagination yet → one run
  reaches back ~4 y/ticker.
- **EU conid resolution**: constituent symbols are SSGA local tickers (SIE, SU, IBE, "NDA FI"…); the
  per-name secdef search must map to the right exchange (XETRA / Euronext / BME / Borsa Italiana).
  The SX5E seed also carries a **non-equity line `VGM6`** (a Euro Stoxx 50 *future* used for cash
  equitisation) that must be dropped before resolution.
- Adding ~550 tickers × ~4 y worsens the known `daily_bar` file-count blow-up — coordinate with
  [`daily-bar-compaction`](daily-bar-compaction.md) (419 755 files / 4.9 GB, 1 row/file).

## Proposed work

1. **Fix the Gateway-path slowness** (the real blocker): bound the `conid=0` warmup with a short
   timeout and **skip it on 503**; treat farm-503s as fast-fail (already partly the intent — verify
   it isn't burning backoff per window). Re-measure per-ticker latency.
2. **Clean the SX5E seed**: drop `VGM6` (non-equity); confirm each EU ticker resolves to the correct
   IBKR conid/exchange (log misses, never guess).
3. **Run the backfill** for SPX + SX5E constituents (attended Gateway after the fix, or the
   **unattended OAuth path** once the Self-Service portal is unblocked — see
   [[live-eod-spine-credential-blocked]]; the 400/501 wall is the reason this isn't already automated).
4. **Acceptance**: Tab-1 per-component candlestick renders for the bulk of SPX + SX5E names; unresolved
   tickers are logged (labeled gap), not silently dropped.

## Notes

- For **front UX testing meanwhile**, the demo store `data/_demo/` (gitignored) has index candlesticks +
  nappes + the weighted list; per-component candlesticks are the only gap there too.
