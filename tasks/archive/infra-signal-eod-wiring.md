> **LANDED 2026-06-14** (branch `infra-signal-eod-wiring`). The EOD analytics stage
> (`orchestration/eod_stages.py::_analytics`) now fires `persist_signal_set` at each captured
> index's session close, after its grid is persisted — so `strategy_signals` lands every banked
> day and S1's ρ̄ entry stops reading an empty partition. The signal params got a typed home:
> `SignalEntryConfig`, a nested block on `UniverseConfig` authored in `configs/universe.yaml`
> (folds into `config_hashes["universe"]`; only that bundle hash moved, section isolation held);
> `signal_config_for` joins the fired-index identity onto it. Gate green (2061 passed, 12 skipped;
> ruff/mypy/lint-imports clean). **Still open** (unchanged): the realized-correlation kill reading
> and the ρ̄ coverage-bias floor — both noted in `infra-signal-layer.md`. Original spec below.

---

# infra-signal-eod-wiring — wire the signal layer into the daily EOD batch + a typed home for its params

**Lane:** `infra-` (with a `core-` config slice). **Depends on:** [infra-signal-layer](archive/infra-signal-layer.md) (landed).

## Why

The signal layer (`infra/signals/*`) is built, tested, and look-ahead clean, but
`persist_signal_set` has **no production caller** — the same dormant state the per-side
surfaces and S1 were in before they were wired. Until the EOD batch computes a signal on a
real banked day, S1's ρ̄ entry reads an empty `strategy_signals` partition every day: "the
flagship can fire" but never does. And `SignalConfig` is a bare dataclass a caller hand-builds
— the reference tenor, slope pillars, lookback windows and basket size have no typed config
home, violating ADR 0028 (no business parameter as a `.py` literal).

## Scope

1. **Typed config home (core).** Add `SignalEntryConfig`, a nested block on `UniverseConfig`
   authored in `configs/universe.yaml` (it sits next to the S1 dispersion sizing
   `dispersion_top_n`/`constituent_top_n` already there, and folds into
   `config_hashes["universe"]` — only that bundle hash moves, the moneyness-grid precedent).
   Fields: `reference_tenor`, `term_slope_front`/`term_slope_back`, `iv_history_lookback_days`,
   `realized_vol_lookback_days`, `periods_per_year`, `basket_size`.
2. **Builder (infra).** `signal_config_for(entry, *, index, provider)` maps the typed nested
   config + the fired index identity to the per-index `SignalConfig` the signal layer consumes.
3. **EOD wiring (infra).** In `default_stages_builder`'s `_analytics` stage, after each fired
   index's grid is persisted, call `persist_signal_set` for that index at its own session
   close (`fired_index.as_of`) — mirroring how the projection grid is persisted in the same
   stage. No new pipeline stage; no new math.

## Verification

- A real production-deps fire (`default_stages_builder` + injected 1C basket source) persists a
  non-empty `strategy_signals` partition for the captured index — the seam test, failing on the
  pre-wiring code (empty partition).
- The config load path resolves the authored `signals:` block into the typed nested model;
  the validator rejects equal slope pillars.
- The two moved hash oracles (`universe` + folded `config_hash`) are re-pinned; goldens that
  brand the universe hash are regenerated (`--regen-golden`) — additive-only, zero economic
  churn.
- Full gate green.

## Honest seams left after this

- ρ̄ still needs constituent surface coverage on the banked day to compute; an index-only
  capture yields term-slope (and, with history, IV-rank/RV−IV) but no ρ̄ — correct, a labelled
  absence, not a fabricated value.
- The coverage-bias hardening (a ρ̄ coverage floor) remains a follow-on.
