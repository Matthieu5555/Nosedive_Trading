# 1H — QC of the index grid: per-tenor coverage floor + Δ-band completeness

> **Phase 1 QC plane.** The roadmap's acceptance for 1H is two-sided: the QC gates
> pass on a clean grid, and the alerts fire on a missing partition or a coverage breach.
> Today's QC plane is ten *instrument-agnostic* checks; none of them knows the index
> grid exists. This task adds the two grid-aware checks the roadmap names and wires
> their breach into the alert layer that already exists.

- **Owns:** the new grid checks in `packages/infra/src/algotrading/infra/qc/checks.py`
  (a per-tenor coverage-floor check and a Δ-band-completeness check), their cut-offs on
  `qc/thresholds.py`, their typed config block (ADR 0028 — see Gotchas), the report/escalation
  wiring in `orchestration/qc_job.py`, and the coverage-breach alert in
  `orchestration/alerts.py`. Conforms to **[ADR 0010](../.agent/decisions/0010-qc-validation-merge.md)**
  (one `triage_records` table) and **[ADR 0028](../.agent/decisions/0028-configuration-and-reproducibility-standard.md)**
  (thresholds from typed config, no `.py` literals).
- **Depends on:** **1F** — the projection onto the (tenor × delta-band) grid is the thing
  these checks validate; the grid record (`ProjectedOptionAnalytics`) and its tenor/delta-band
  fields are 1F's. **1C** — the daily-close partitions these checks read and the
  missing-partition alert names. **P0.1** — the pinned tenor grid (OQ-4: 10d, 1m, 3m, 6m, 12m,
  18m, 2y, 3y) as a data contract / config, which the coverage floor is keyed on.
- **Blocks:** nothing downstream builds on 1H, but it is the acceptance gate for the Phase 1
  pipeline: 1G's cron run is not "green" until the grid passes QC and a breach pages.
- **State going in (audited 2026-06-07):** `qc/checks.py` defines ten generic checks
  (`CHECK_COLLECTOR_CONTINUITY` … `CHECK_SCENARIO_COMPLETENESS`) — all instrument-agnostic.
  The closest, `check_option_chain_coverage`, gates a single underlying's *flat* chain against
  one scalar `min_chain_count`; it has **no notion of per-tenor floors or a delta band**.
  `orchestration/alerts.py` already has `missing_partition_alerts` (names an absent
  `(trade_date, underlying)` partition, no interpolation) and `qc_fail_alert` (fires on a
  `page` escalation). `qc/thresholds.py` still carries its supplementary cut-offs as module-level
  `DEFAULT_*` `.py` literals — an ADR-0028 gap this task must not extend.

## Objective

The QC plane validates the index grid as a grid, not as a flat chain. Two new checks:
(1) a **per-tenor coverage floor** — for each pinned tenor on the grid, the count of usable
projected option points clears that tenor's floor, and a tenor whose floor is breached is named
with its measured-vs-floor count; (2) **Δ-band completeness** — for each tenor the selected
strikes actually span the 30Δ-put → ATM → 30Δ-call window with no hole inside the band, and a
gap is named by tenor and the missing band region. Both emit a `QcResult` that rolls into the
existing report/escalation, lands in the one `triage_records` table (ADR 0010), and on breach
trips a coverage-breach alert beside the existing missing-partition alert. Thresholds come from
typed config (ADR 0028). Gate green.

## What to do (ordered)

1. **Add the two check names and register them.** Extend `CHECK_NAMES` in `qc/checks.py` with
   `CHECK_TENOR_COVERAGE_FLOOR = "tenor_coverage_floor"` and
   `CHECK_DELTA_BAND_COMPLETENESS = "delta_band_completeness"`. Keep them in declared order with
   the existing ten so the report enumeration is stable.

2. **Write `check_tenor_coverage_floor`.** It takes the day's grid points for one underlying
   (1F's `ProjectedOptionAnalytics` rows, grouped by tenor), the pinned tenor grid, and the
   per-tenor floors from config. For each pinned tenor it counts usable points and compares to
   that tenor's floor (`>=` passes — boundary-exact passes, per the thresholds convention). A
   tenor *absent entirely* is a breach, not a skip. The result names the **specific breaching
   tenors** with measured-vs-floor counts in `context` (mirror `check_option_chain_coverage`'s
   "name the missing contracts" style — an operator must see *which* tenor, not "coverage low").
   `measured_value` = the worst (lowest count − floor) margin across tenors.

3. **Write `check_delta_band_completeness`.** For each pinned tenor, take the selected strikes'
   deltas and assert they span the configured band (30Δ put → 30Δ call) and have no interior
   gap wider than the configured max step. Name the tenor and the missing band region for any
   gap. The band edges and max-step come from config, **not** from the points themselves
   (independent of the data under test — a thin chain must *fail*, not silently define its own
   band). Empty tenor, single-strike tenor, and all-strikes-one-side are explicit breaches.

4. **Thread the cut-offs through typed config (ADR 0028).** Add a typed config block carrying the
   per-tenor floors (a tenor → floor mapping, keyed on the P0.1 pinned grid), the band edges, and
   the max interior delta step; surface them on `QcThresholds`. **Do not** add new module-level
   `DEFAULT_*` `.py` literals — fold these into the typed/hydrated path so the new checks set the
   ADR-0028 precedent the existing supplementary defaults still violate. A missing per-tenor floor
   for a pinned tenor is a config error (raise, don't default to zero).

5. **Wire into `qc_job.run_qc`.** The grid checks run when the job is handed the grid points
   (pass them like `extra_results`, or build them in-job from a grid input mirroring the
   `collector_summary` injection). Their `QcResult`s roll into `build_report`/`escalation_level`
   unchanged and persist to `qc_results`; triage mapping (`triage_from_qc`) already routes any
   non-pass into `triage_records` — confirm the two new names flow through, do not add a path.

6. **Add the coverage-breach alert.** In `orchestration/alerts.py` add
   `ALERT_COVERAGE_BREACH = "coverage_breach"` and a `coverage_breach_alerts(report)` that fires
   one named alert per breaching tenor (subject = `underlying@tenor`, detail = measured-vs-floor),
   reading the new checks' results out of the report — reuse the report, do not recompute coverage.
   Keep `missing_partition_alerts` as the orthogonal "partition absent" signal; coverage-breach is
   "partition present but too thin". Both must be reachable from the 1G run's alert evaluation.

## Test surface

Read [TESTING.md](TESTING.md). The expected values are derived independently of the check code —
build the grid fixture by hand from the pinned tenor grid and a known delta band, count by hand,
and assert the named breach. Specific named cases (extend `test_qc_checks.py`, alerts in
`test_orchestration.py`, triage routing in `test_seam_triage.py`):

- `test_tenor_coverage_floor_passes_when_every_tenor_clears_its_floor` — a full grid fixture,
  all tenors at or above floor, status pass.
- `test_tenor_coverage_floor_names_the_breaching_tenor` — one tenor one below its floor; status
  fail, `context` names that tenor with measured-vs-floor, the passing tenors are not named.
- `test_tenor_coverage_floor_count_exactly_on_floor_passes` — boundary-exact (`count == floor`)
  passes (the thresholds `>=` convention).
- `test_tenor_coverage_floor_absent_tenor_is_a_breach` — a pinned tenor with zero points fails
  and is named (not silently skipped).
- `test_delta_band_completeness_passes_for_full_band` — strikes spanning 30Δ put→call with no
  interior gap, status pass.
- `test_delta_band_completeness_flags_interior_gap` — a hole inside the band wider than the
  configured step; fails and names the tenor + missing region.
- `test_delta_band_completeness_flags_one_sided_chain` — all strikes on the call side; fails
  (does not let the data define its own band).
- `test_delta_band_edge_cases` — empty tenor, single-strike tenor: explicit labeled breach, not
  a crash and not a silent pass (the TESTING.md negative-path floor).
- `test_grid_thresholds_missing_tenor_floor_raises` — a pinned tenor with no configured floor
  raises a config error, never defaults to zero.
- `test_grid_checks_roll_into_report_and_escalation` — both new `QcResult`s flow through
  `build_report`/`escalation_level` and a fail escalates as the existing checks do.
- `test_grid_breach_lands_in_triage_records` — a breach routes through `triage_from_qc` into the
  one `triage_records` table with `source="qc"` (ADR 0010), malformed rejected.
- `test_coverage_breach_alert_fires_per_breaching_tenor` — one alert per breaching tenor, subject
  `underlying@tenor`; a clean grid fires none.
- `test_coverage_breach_and_missing_partition_are_distinct` — a present-but-thin tenor trips
  coverage-breach not missing-partition; an absent partition trips missing-partition not coverage.

## Done criteria

The two grid checks exist in `qc/checks.py`, registered in `CHECK_NAMES`, keyed on the P0.1
pinned tenor grid and the 1B delta band; their cut-offs come from typed config with **no** new
`.py` literals (ADR 0028); they run in `qc_job.run_qc`, roll into the report/escalation, and land
in the one `triage_records` table (ADR 0010); a coverage breach fires a named per-tenor alert
distinct from the missing-partition alert; every named test above is present and asserts the
labeled breach against a hand-derived oracle; root gate green (`uv run ruff && uv run mypy &&
uv run lint-imports && uv run pytest`).

## Gotchas

- **Blueprint (ADR 0011) overrides on domain.** The pinned tenor grid (10d, 1m, 3m, 6m, 12m,
  18m, 2y, 3y) and the 30Δ-put → ATM → 30Δ-call band are the *blueprint/roadmap* definition (OQ-4,
  1B). If a check's grid or band disagrees with the blueprint data dictionary, the blueprint wins —
  do not hardcode a grid from this spec's prose; read the pinned contract from config (P0.1).
- **No look-ahead bias.** These checks judge a single trade date's grid against a *static*
  configured floor/band. The floor must not be derived from the same day's data (a thin day would
  lower its own bar), and never from a *future* day. If a floor is ever baselined from history,
  it is history strictly before the trade date — same discipline as the anomaly baseline.
- **Independent oracle.** The completeness/coverage expected counts in tests are hand-counted from
  a fixture you construct, not read back from the check. Do not assert `check(grid) == grid`.
- **Don't widen the seam.** Triage routing and the report roll-up already exist; the two new
  checks must flow through them with no new persistence path and no second table — one
  `triage_records` table is the ADR-0010 invariant.
- **ADR 0028 precedent.** `qc/thresholds.py` still holds `DEFAULT_*` `.py` literals from before
  the hardening; this task must not add more — put the new cut-offs in typed config and let that
  be the pattern the leftover literals are later pulled into.
- **uv only** for every command (run, test, lint); no bare `python`/`pip`.
