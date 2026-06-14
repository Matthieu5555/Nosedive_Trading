# signals — the daily strategy-entry signal layer

**TL;DR** — the §3 strategy book triggers on four market diagnostics; this module derives them
daily, as-of and look-ahead clean, and persists them as `strategy_signals` rows. A strategy
reads the readings back (via `algotrading.strategy.signal_snapshot_from_store`); it never
imports this module — the layer is blind to alpha (pure infra).

```python
from datetime import datetime, UTC, date
from algotrading.infra.signals import SignalConfig, persist_signal_set
from algotrading.infra.storage import ParquetStore

store = ParquetStore(root)
config = SignalConfig(
    index="SX5E", provider="IBKR", reference_tenor="3m",
    term_slope_front="1m", term_slope_back="6m",
    iv_history_lookback_days=365, realized_vol_lookback_days=30,
)
persist_signal_set(
    store, config, as_of=date(2026, 6, 12),
    calc_ts=datetime(2026, 6, 12, 22, 0, tzinfo=UTC),
    config_hashes={"signals": "<bundle-hash>"},
)
```

## The four signals (TARGET §4 R3 / §3)

Each is a pure function, independently testable; the orchestrator wires them to the as-of store.

- **Implied correlation ρ̄** (`correlation.py`, S1) — the inverse of the Eq. 23 basket-variance
  identity: given the index ATM vol and its constituents' ATM vols/weights, back out the single
  average correlation the market prices. Closed-form (`rho_bar = (σ²_I − own) / cross`), no
  root-finder; `cross` is exactly the term `risk/basket.py` multiplies by `avg_correlation`, so
  the two round-trip. Persisted **per tenor**, subject = the index.
- **Term-structure slope** (`term_structure.py`, S5) — `σ_atm(back) − σ_atm(front)`; positive in
  contango. Persisted per subject (index + each name), keyed by a `front:back` pillar pair.
- **Realized-vs-implied spread** (`realized_volatility.py`, S2/S3) — annualized realized vol from
  a trailing window of closes (`daily_bar`), minus the reference-tenor implied. Per subject.
- **IV rank / percentile** (`iv_history.py`, S3) — where the current ATM IV sits in its banked
  trailing window: rank `(current − min)/(max − min)` and percentile (fraction strictly below).
  Per subject. Rank is the persisted reading; percentile is a tested helper for callers.

A signal the inputs cannot answer (a degenerate basket, a flat IV window, too few bars, a
missing pillar) is **omitted** — a labelled absence, never a fabricated value.

## The persisted contract

`StrategySignal` (`contracts/tables.py`, table `strategy_signals`, layer `signals`,
provider-partitioned). One row per `(snapshot_ts, provider, signal_kind, subject, tenor_label)`:

- `underlying` — the **book context** (the index, e.g. `SX5E`); the partition's grouping symbol,
  so a strategy reads the whole day's set in one partition. *Not* the name the reading is about.
- `subject` — what the reading is on: the index for ρ̄, a constituent for a per-name reading.
- `signal_kind` — the `SignalKind` value as a plain string (infra is blind to the strategy enum;
  the values are mirrored as constants here and pinned by a strategy-layer test).
- `tenor_label` — the tenor (`3m`), or a `front:back` pair for a term slope.
- `value` — the scalar; not sign-constrained (ρ̄ can fall outside `[-1, 1]`; spreads are signed).

## Config (`SignalConfig`, injected — ADR 0028)

| field | meaning |
|-------|---------|
| `index` / `provider` | the index whose signals are computed, and the source whose surfaces feed them |
| `reference_tenor` | the single tenor the per-name range signals (IV-rank, RV−IV) are taken at |
| `term_slope_front` / `term_slope_back` | the two pillars the term slope spans |
| `iv_history_lookback_days` / `realized_vol_lookback_days` | calendar-day trailing windows |
| `periods_per_year` | realized-vol annualization (default 252, the trading-day convention) |
| `basket_size` | the ρ̄ universe — `None` = the full as-of basket, an int = top-`n` by weight |

## As-of / look-ahead discipline

Every read is gated by `as_of`: surfaces and bars at or before the date, the live partition only
(no future restatement), and the basket as it stood that day (`universe.members`, the ASOF-join
resolver). A replay of an old day resolves only that day's data. **Caveat:** ρ̄ is computed over
the constituents that actually have a weight *and* a surface that day; incomplete coverage
biases it (the cross term is understated) — R2-grade per-name coverage is assumed.

## Where the readings go

`algotrading.strategy.signal_snapshot_from_store` reads one day's partition back into the
`SignalSnapshot` the strategy harness injects — the seam that took S1's ρ̄ entry from dormant
(fixture-fed) to live. That reader lives in the strategy layer because it touches both sides;
infra stays blind to alpha.
