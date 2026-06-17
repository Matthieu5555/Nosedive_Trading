# frontend-signals-bff — expose the persisted signal layer through the BFF

**Lane:** `frontend-` (BFF + web delivery). **Layer:** `apps/frontend` (reads down into
`packages/infra` only). **TARGET:** §7 #7 (signal layer) — the read/serialize half.

## Why

`infra-signal-layer` landed the compute + daily persistence: the `strategy_signals` table
(`StrategySignal` contract, layer `signals`, provider- and run-partitioned) holds, per name
and as-of, the four entry-input signals — implied correlation ρ̄ (R3), IV rank, realized-vs-
implied spread, term-structure slope. The strategy book already reads them back in-process
(`strategy/signal_data.py`). Nothing exposed them over HTTP, so the Signals web page (sibling
**F-SIG**) had no source. This slice is the BFF read surface: it reads the persisted partition
and serializes it. **It recomputes no signal math** — read-only over what the EOD cron banked.

## Endpoints

Mounted additively under `/api/signals` (router prefix), tag `signals`.

### `GET /api/signals/underlyings`

The index underlyings that have a persisted `strategy_signals` partition (the `underlying`
column is the *index*, never the per-name `subject`). Matches `/api/surfaces/underlyings`.

```
{ "underlyings": ["SX5E"] }
```

### `GET /api/signals?underlying=<idx>&trade_date=<YYYY-MM-DD>&run_id=<hex>`

The full as-of signal set for one index. `underlying` defaults to the context default index;
`trade_date` absent resolves the latest persisted partition for that index; `run_id` pins one
fetch (absent → newest fetch, the store's default). A missing partition is a labelled-empty
body (`n_signals == 0`, HTTP 200); a malformed `trade_date` a labelled `400`
(`error: "bad_trade_date"`, the shared `BadRequestError` path).

Response (one row per persisted signal, plus a `by_kind` index so F-SIG can key off kind
without re-grouping):

```
{
  "underlying": "SX5E",
  "trade_date": "2026-06-16",
  "snapshot_ts": "2026-06-16T15:30:00+00:00",   // null when empty
  "n_signals": 145,
  "kinds": ["implied_correlation", "iv_rank", "iv_vs_realized", "term_structure_slope"],
  "signals": [ <signal>, ... ],                 // every row, stable-sorted
  "by_kind": {
    "iv_rank": [ <signal>, ... ],
    "iv_vs_realized": [ ... ],
    "term_structure_slope": [ ... ],
    "implied_correlation": [ ... ]
  }
}
```

A `<signal>` is the serialized `StrategySignal` plus a `label`/`unit` pair derived from its
`signal_kind` (display metadata only — not recomputed values):

```
{
  "signal_kind": "iv_rank",
  "label": "IV rank",
  "subject": "SX5E",            // the name the reading is about (index or constituent)
  "tenor_label": "3m",
  "value": 0.24004,
  "unit": "fraction [0,1]",
  "snapshot_ts": "2026-06-16T15:30:00+00:00",
  "source_snapshot_ts": "2026-06-16T15:30:00+00:00",
  "provenance": { "calc_ts": ..., "code_version": ..., "config_hashes": {...},
                  "stamp_hash": ..., "n_sources": N }
}
```

`signal_kind` → `(label, unit)` is the single display map in the router:

| signal_kind | label | unit |
|---|---|---|
| `iv_rank` | IV rank | `fraction [0,1]` |
| `iv_vs_realized` | Realized − implied | `vol points (annualized)` |
| `term_structure_slope` | Term-structure slope | `vol points (back − front)` |
| `implied_correlation` | Implied correlation ρ̄ | `correlation [-1,1]` |

`subject` carries the name; for the per-name signals (`iv_rank`, `iv_vs_realized`,
`term_structure_slope`) it is the index *or* a constituent; for `implied_correlation` it is the
index (ρ̄ is an index-level reading, one per tenor). `tenor_label` is `3m` etc. for the per-name
signals, the front:back pair (`1m:6m`) for the slope, and the index tenor for ρ̄.

## What is NOT here

- **IV percentile.** `infra/signals/iv_history.py` *has* `iv_percentile`, but the EOD layer
  persists only `iv_rank` (`SIGNAL_KIND_IV_RANK`). This slice is read-only; persisting
  percentile is the signal-layer's job, not the BFF's. Surfacing it here would mean
  recomputing, which this slice forbids. When the layer banks it, the row flows through
  unchanged (the display map gains one entry).
- No new compute, no aggregation across dates, no re-derivation of any value.

## Test surface

Contract tests at the BFF edge (`apps/frontend/tests/test_signals_api.py`), seeded against a
temp store (never canonical `data/`), expected values derived independently from the seeded
`StrategySignal` rows:

- empty store → `n_signals == 0`, `signals == []`, `by_kind == {}`, `snapshot_ts is None`,
  HTTP 200.
- bad `trade_date` → 400, `error == "bad_trade_date"`.
- populated → field names/units per the table above; `n_signals` == seeded row count;
  `by_kind` partitions the rows exactly; `value` round-trips; `subject`/`tenor_label` carried.
- `/underlyings` lists exactly the index `underlying`s present, not the per-name `subject`s.
- latest-partition resolution: two dates seeded, no `trade_date` → the newer date's rows.
- units mapping is total over the four persisted kinds; an unknown kind falls back to a
  labelled `unit: null` rather than raising.

## Files

- `apps/frontend/src/algotrading/frontend/routers/signals.py` (new router)
- `apps/frontend/src/algotrading/frontend/serializers.py` (`strategy_signal_to_dict`)
- `apps/frontend/src/algotrading/frontend/app.py` (additive `include_router`)
- `apps/frontend/tests/test_signals_api.py` (new)
- `apps/frontend/README.md` (API list), `TARGET.md` §7 #7 state note.

## State

Landed 2026-06-16. Gate green. F-SIG (web Signals page) consumes `/api/signals` +
`/api/signals/underlyings`.
