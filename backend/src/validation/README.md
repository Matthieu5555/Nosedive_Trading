# validation — the anomaly / triage plane

This package is the sibling of `src/qc`. Where QC asks static, per-object questions
("did *this* surface fit pass its RMSE cut-off?"), validation asks a rolling-baseline
question: "did this run's metrics shift *abnormally* versus their own recent history?"
A run can pass every static QC check and still be anomalous — a usable-quote count
that quietly halves, a fit error that creeps up day over day — and catching that is
the depth this plane adds. Both planes are pure: `run_id` and `as_of` are injected,
never read from a clock, so a validation pass reproduces byte-for-byte in replay.

The two planes are deliberately separate, but their *results* must not be. Anything
consuming quality output — an operator's triage list, an alerting layer — should see
one shape, ordered by one rule, escalated by one policy. So this package also owns the
collapse: it folds a QC report and a validation outcome into one worst-first list of
`contracts.TriageRecord` rows.

## The two planes and the one table

```text
  Qc checks (src/qc) ── QcReport ──┐
                                   ├──> build_triage ──> (TriageRecord, ...) ──> triage_records table
  metrics + baselines ── run_validation ── ValidationOutcome ──┘                  (persisted via storage)
```

This diagram shows where a triage row comes from; it omits the orchestration job that
assembles the metrics and writes the table (that lives in `src/orchestration`, not
here). The named QC checks produce a `QcReport`. Independently, `run_validation` scores
a run's tracked metrics against their rolling baselines and produces a
`ValidationOutcome`. `build_triage` merges both into one ordered list of
`TriageRecord`. The records are pure values; persisting them to the `triage_records`
table is the caller's job — this plane never does I/O.

## Fastest path: score a run and triage it

    from validation import AnomalyThresholds, run_validation, build_triage, escalation_level

    outcome = run_validation(
        run_id=run_id, underlying="AAPL", as_of=as_of,
        current_metrics={"n_iv_points": 412.0, "max_slice_rmse": 0.004, ...},
        baselines={"n_iv_points": [...recent history...], ...},
        thresholds=AnomalyThresholds(),
    )
    records = build_triage(qc_report=qc_report, validation=outcome)  # both planes, one list
    level = escalation_level(records)                                # none / notice / page

`current_metrics` is whatever the run measured (the analytics outputs carry these);
`baselines` is the recent history of the same metrics (read from storage). Neither is
fetched here, which is what keeps `run_validation` a pure function of its inputs.

## The anomaly verdict: four states, not two

A metric is not simply "normal or not". Cold-start runs have too little history to
judge, and pretending that is "normal" is exactly how an abnormal first run slips
through. So an anomaly score is one of four states:

| Status        | Meaning                                            | In triage? |
| ------------- | -------------------------------------------------- | ---------: |
| `normal`      | within the band                                    |         no |
| `warn`        | `|robust z| >= warn_z` (default 3.5)               |        yes |
| `fail`        | `|robust z| >= fail_z` (default 5.0)               |        yes |
| `no_baseline` | fewer than `min_baseline` (default 10) prior points |         no |

The score is a robust **median/MAD** z-score, not a mean/standard-deviation one, so a
single earlier spike in the baseline cannot inflate the scale and mask a new one. The
sign is kept (a collapse reads negative, a blow-out positive) but the bands compare on
magnitude. A `no_baseline` outcome carries `robust_z=None` — it could not be judged, so
it must not pretend to a number; the dataclass enforces that coupling.

Worked example: with the baseline `[10, 11, …, 21]` the median is `15.5` and the MAD is
`3.0`, so `robust_z(value) = (value - 15.5) / (1.4826 · 3.0)`. A value of `33` scores
`3.93` (a `warn`); a value of `40` scores `5.51` (a `fail`); a value on `15.5` scores
`0` (`normal`).

## How the results collapse, and what survives the collapse

`build_triage` turns both planes into `TriageRecord` rows. The specificity discipline
that QC enforces survives the merge intact: a QC row's `target_key` and `detail` come
from the *same* `qc.result_headline` / `qc.named_offender` an operator already reads, so
"surface_fit_error fail [failing_maturity=0.5]" does not decay into "QC red" on the way
in. A validation row names the metric that moved (`target_key="metric=n_iv_points"`).

Each record carries a `source` (`"qc"` or `"validation"`), a `severity`, and a
`reason_code`. Validation rows have no severity of their own, so one is derived: a hard
`fail` is `critical` (page-worthy), a `warn` is `warning`.

### Escalation: one policy, both planes

`escalation_level` collapses a record list to a single signal an alert layer thresholds
on:

| Condition                                   | Level    |
| ------------------------------------------- | -------- |
| any `fail` with `critical` severity         | `page`   |
| any other `fail`, or any `warn`             | `notice` |
| nothing flagged                             | `none`   |

This is the QC plane's existing rule, widened to span both planes. A QC critical fail
and a validation fail both page; a warning-severity QC fail and a metric `warn` both
notice.

## Failure and edge behavior

- **Too little history** is `no_baseline`, reported and excluded from triage — never
  silently treated as normal.
- **A degenerate baseline** (every prior value equal, MAD = 0) scores `0` on the median
  and `±inf` off it, so any departure is an unambiguous `fail`.
- **A mis-tuned config** fails loudly at construction: `AnomalyThresholds` rejects
  `fail_z < warn_z` and `min_baseline < 1`.
- **An unexplained flag is impossible to build**: a non-`pass` `ValidationCheck` without
  a `reason_code` raises at construction — the banner this plane exists to prevent.
- **A malformed record is rejected at the storage seam**, not coerced: a `TriageRecord`
  with a naive (non-tz) `run_ts` raises `ContractValidationError` on write.

## What this plane does *not* do

It does not re-implement QC's named checks; those stay in `src/qc`. It does not assemble
the metrics or read baselines or write the table; that wiring is the orchestration
layer's job (mirroring how `src/qc` is pure and `orchestration/qc_job.py` operates it).
And it does not gate individual quotes — quote-level quality lives in
`snapshots/quote_quality.py`.

## Layout

| File          | What it owns                                                            |
| ------------- | ----------------------------------------------------------------------- |
| `anomaly.py`  | the rolling-baseline robust-z detector and its thresholds/outcome types |
| `state.py`    | the run-level validation contracts (`ValidationCheck` / `Report`)       |
| `engine.py`   | `run_validation` — score a run's metrics into a `ValidationOutcome`     |
| `triage.py`   | the unified `TriageRecord` collapse and the one escalation rule         |
