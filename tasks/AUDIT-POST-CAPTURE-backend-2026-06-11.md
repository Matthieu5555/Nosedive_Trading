# Post-Capture Backend Audit — Reconciled Findings Report
**Date:** 2026-06-11 · **Repo:** /srv/project · **Branch:** feature/marimo-app-notebooks
**Scope:** 101 findings surviving 3-vote adversarial verification. No code was changed by this audit.

---

## 1. Executive Summary

**Overall health verdict:** The rushed post-capture run landed **functionally coherent** — the spine captures, persists, and serves. But the audit surfaces one **systemic, repeatedly-confirmed ADR-0040 violation** (silent-skip of empty-with-raw outputs in `persist_outputs`) and a cluster of **silent-default / loud-not-silent gaps** (ADR 0028 config presence, ADR 0019 skips) that make the system *correct-when-configs-are-complete* rather than *defended-against-incomplete-input*. Documentation has drifted materially behind the ADR-0041 overwrite-rerun pivot and the ADR-0039 bridge landing. **Two high-`break_risk` refactors** (eod_stages closure side-channels, persist_outputs sentinel) touch the hot persistence path and need care.

After reconciling duplicates, the **101 raw findings collapse to ~90 distinct items**. The single most-reported issue — `persist_outputs` silent-skip — appears **7 times** across dimensions/auditors (F-ACTOR-01 below).

### Counts by severity (distinct findings)
| Severity | Count | Notes |
|---|---|---|
| blueprint-adr-violation | 8 | dominated by the de-duplicated persist_outputs cluster (7 reports → 1) + ProjectionGap discard + notebook bridge duplication + forward implied_carry + health.status + version-leak + spot-skip |
| drift | ~45 | docs-vs-behaviour + convention splits |
| gap | ~22 | missing tests, missing flags, missing config threading |
| nit | ~14 | cosmetic / dead params / magic sentinels |

### Counts by dimension
- **Dim 1 (loud-not-silent / lookahead / correctness):** ~38 — the heaviest and highest-value cluster.
- **Dim 2 (contract/blueprint conformity):** ~20
- **Dim 3 (library-leverage / DRY / REP):** ~6
- **Dim 4 (test quality / docs / hygiene):** ~28
- **Dim 5 (operational robustness / library transports):** ~12

### The 5–8 most important items
1. **F-ACTOR-01 — `persist_outputs` silently skips empty-with-raw tables** (`driver.py:1006-1009`, blueprint-adr-violation, ADR 0040 #3). Reported 7×. The named SX5E 2026-06-10 symptom. **Top priority.**
2. **F-BFF-01 — Gamma$ served 100× too small** (`serializers.py:218-246`, drift). Double `/100` after the engine already converted to one_pct. Wrong number on the live BFF; masked by a hand-built test fixture.
3. **F-BFF-02 — health router reads `row.status`; contract field is `qc_status`** (`health.py:40`, blueprint-adr-violation, ADR 0029). **Runtime AttributeError** for any trade_date with stored qc_results. Quick one-line fix.
4. **F-STORE-01 — restatement files leak into live-only reads** (`adapter.py:317`, blueprint-adr-violation). `'version=' not in p.parts` is always true → live+restated double-counting. Quick one-line fix; identical correct check already exists at line 359.
5. **F-CORE-01 / F-QC-01 — economic config blocks silently default** (`loader.py:106-120`, `platform_config.py:287-435`, drift). Contradicts the repeated "loader requires the block present" ADR-0028 docstrings. Production is safe only because shipped YAML happens to be complete.
6. **F-SURF-01 — projection discount-factor silently falls back to DF=1.0** (`projection.py:251-253`, drift). Pinned-tenor keys never match listed-expiry keys → every projected cell priced as if rates were zero, no flag. **ADDENDUM 2026-06-12: this is a *downstream symptom*, not the root.** The pinned-tenor keys never match listed-expiry keys because the capture never selects expiries at the pinned tenors — it keeps only the nearest ~8 (all 1–2 weeks out). Broker DOES list 2y/3y (SPX→2031, SX5E→2035, measured live). Root fix = [`T-tenor-selection.md`](archive/T-tenor-selection.md); projection/BFF half (this item + SVI degeneracy + F-BFF-03/04) = [`T-vol-surface-correctness.md`](archive/T-vol-surface-correctness.md). **F-IBKR-02's "sort chronologically before slicing" does NOT fix the root** (sorted nearest-8 is still nearest-8). Do not close F-SURF-01 / F-IBKR-02 green in isolation.
7. **F-LOOKAHEAD cluster — membership reads omit `known_as_of`** (`history_backfill.py:128`, `constituents.py:92-94`, drift/gap). Survivorship/look-ahead bias on both backfill and BFF read paths.
8. **F-COLLECT-01 — live vs replay sequence stamping diverges on non-monotonic exchange_ts** (`collectors/live.py:52-77`, drift). Breaks the exactly-once / byte-identical re-capture guarantee (ADR 0027) under out-of-order arrival; untested.

---

## 2. Confirmation of the Rushed-Run Changes

| Change | Verdict | Evidence |
|---|---|---|
| **ADR 0039 — raw schema bridge** (`sample_bridge.contracts_to_events` / `events_to_contracts`) | **Landed coherently, but FLAGGED for a second copy.** | The bridge exists, is exported, and `export_sample.py` uses it (per F-SCRIPT-02). However **F-NB-01 (blueprint-adr-violation)**: `notebooks/apps/_shared.py:107-199` re-implements the "ONLY converter" field-for-field instead of routing through the bridge — a forbidden second copy that will drift. Also **F-SCRIPT-02 (drift)**: `scripts/README.md` still claims the bridge does not exist ("no translation layer exists in packages/infra today"), stale against the landed code. |
| **ADR 0040 — ingestion invariants (complete-or-flagged)** | **FLAGGED — the headline invariant is violated in the live path.** | The named bug `if not records: continue` is live at `driver.py:1006-1009` (**F-ACTOR-01**, 7 reports). Compounded by F-ACTOR-02 (no-spot skip, bare `continue`), F-ACTOR-03 (`ProjectionGap` results discarded), F-IBKR-08 (qualified-but-no-marks returns quote-less basket), F-ORCH-04 (manifest records output_partitions for indices that produced nothing), F-ORCH-05 (partial multi-basket failure leaves raw-but-no-derived unflagged), F-QC-02 (`underlying_quote_health` PASS on zero quotes), F-BFF-03/04 (silent 0.0 grid fills). The reconstruction/replay path *does* honour MISSING vs EMPTY — only the **live capture path is out of conformance.** |
| **ADR 0041 — overwrite re-fire (ledger records, never gates)** | **Landed coherently in behaviour; DOCS FLAGGED as systematically stale.** | The body correctly re-runs all five stages unconditionally and `skipped` stays empty. But the contract docstrings/READMEs still describe the *old* skip-based idempotent restart in **5 places**: F-ORCH-06 (`pipeline.py:7-13`), F-ORCH-07 (`pipeline.py:118-203`), F-ORCH-08 (`README.md:19-23`), F-ORCH-09 (`run_state.py:194-213` "resume key"), F-OPS-04 (`pipeline.py` module/EodResult/run_end_of_day docstrings). Also F-ORCH-10 (drift): ledger winner picked by append order not `recorded_ts` — a determinism gap under concurrent appenders. |
| **429 transport backoff** (the 2026-06-10 fix) | **Landed and operational, but FLAGGED as un-reconciled / un-configurable.** | The fix works. But **F-IBKR-03 (drift)** and **F-IBKR-07 (gap)**: the transport's `_DEFAULT_MAX_RETRIES=6` / `_DEFAULT_BACKOFF_BASE_S=0.5` are `.py` literals not threaded from `ibkr_history.yaml`'s typed `RetryConfig`, and they **stack** with the collector's own retry (can compound into minutes/window). F-OPS-05 (gap): `gateway_keepalive.py` / `eod_babysitter.py` bypass `CpRestTransport` entirely with bare `httpx.Client` (no 429 backoff, swallowed exceptions). F-CONN-04 (gap): three httpx transports, only CpRest has retry. |
| **Front `ALGOTRADING_DATA_ROOT` override** | **No direct finding flags the override mechanism as broken.** | The override itself is not flagged. Adjacent gap **F-SCRIPT-04 (nit)**: `ingest_membership.py --store-root` defaults to cwd-relative `./data` and does *not* honour `ALGOTRADING_DATA_ROOT`/`_REPO_ROOT`, so it can write outside the canonical store. The data-root discipline is therefore inconsistent across entrypoints, not wrong on the front read path. |
| **QA-fix set (just-committed): ADR 0028 config, 0036 dollar-greeks, 0027 stamping, REP1/REP2** | **Partially landed — each has a residual flag.** | **ADR 0028 (config):** migrated to pydantic v2 (reflective seam dropped), but F-CORE-01/F-QC-01 show the load path still silently defaults nested economic blocks, and F-CORE-02 (core README still describes the deleted `build_dataclass`/reflective seam). **ADR 0036 (dollar-greeks):** engine now writes canonical one_pct, but F-BFF-01 (the double-`/100` 100×-too-small bug) and F-RISK-04 (stock-leg `dollar_gamma=0.0` with `unit=None`) remain. **ADR 0027 (stamping):** F-COLLECT-01 — live arrival-order vs replay canonical-order divergence breaks byte-identity under non-monotonic exchange_ts. **REP1/REP2:** F-DEP-01 (drift) — `polars`/`pandas` still declared with zero imports; REP2 unified `as_of` on DuckDB QUALIFY, *not* polars, so polars has no home and the pyproject comment is false. (`pycryptodome` part of REP0 *is* resolved.) **REP5 not landed** (F-BFF-05). |

---

## 3. Findings by Subsystem

> **Reconciliation note:** Seven findings report the same `persist_outputs` bug at `driver.py:1006-1009` (subsystems actor/driver, orchestration, actor-analytics, storage-snapshots ×2, plus op-robustness ×2). They are merged into **F-ACTOR-01**. Two `_warmup_poll_batch` regression findings (cp_rest_close_capture.py:183-194 / 185-193) merge into **F-IBKR-05**. Two multi-basket `_analytics` overwrite findings merge into **F-ORCH-02**. Two `ForwardCurvePoint.implied_carry` findings (one drift dim-2, one blueprint-adr dim-2) merge into **F-SURF-02** (kept at the stronger blueprint-adr-violation severity). The two import-linter execution/strategy findings merge into **F-EXEC-01**.

### CORE / CONFIG
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-CORE-01 | drift | 1 | `core/config/loader.py:106-120` | Loader enforces nested economic-block presence (ADR 0028) → only top-7 sections enforced; nested blocks/scalars carry pydantic defaults and fill silently when YAML omits them | Enforce nested-presence (drop defaults on load path or add required-keys check), or correct every "never silently defaulted" docstring | medium |
| F-QC-01 | drift | 2 | `core/config/platform_config.py:287-435` | qc.yaml nested blocks required-present → grid/continuity/forward_engine/fit_tolerance/anomaly use `default_factory`; missing `anomaly:` hashes+runs on `.py` literals | Drop `default_factory` on nested blocks or add nested-presence validation | medium |
| F-CORE-02 | drift | 4 | `core/README.md:23-24` | Describe pydantic v2 seam (post-ADR-0028) → still describes deleted `build_dataclass`/"reflective seam" | Rewrite lines 19-24 to pydantic v2 (extra=forbid/strict/frozen) | low |
| F-CORE-03 | nit | 4 | `core/config/platform_config.py:1-9` | 7 hashed sections → docstrings say "four sections/four versions"; `config_snapshot` lists only six (omits monetization) | Update wording to seven; add monetization | low |

### ACTOR / ANALYTICS / DRIVER
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| **F-ACTOR-01** (merged ×7) | **blueprint-adr-violation** | 1 | `actor/driver.py:1006-1009` | ADR 0040 #3: empty-with-raw → explicit empty/flagged + reason code → `if not records: continue` silently drops every empty derived table (named SX5E 2026-06-10 symptom) | Capture path fail-hard on raw-but-no-derived (OQ-C); replay path write EMPTY/MISSING marker; minimum loud per-table log with (trade_date, underlying, table) | **high** |
| F-ACTOR-02 | drift/blueprint-adr | 1 | `actor/driver.py:791-796` (793-796) | ADR 0019: no-spot skip must be queryable → bare `continue` with inline comment, no log/flag/reason | Structured log `no_usable_spot` + flagged projection marker | medium |
| F-ACTOR-03 | blueprint-adr-violation | 1 | `actor/driver.py:803-813` | `project_grid` `result.gaps` (ProjectionGap) routed to triage → `result.gaps` unconditionally discarded; labeled holes never reach 1H checks | Carry gaps out of `_build_projected_analytics` and route to log/triage | low |
| F-ORCH-02 (merged ×3) | drift | 1/4 | `orchestration/eod_stages.py:360-391` | `AnalyticsResult.outputs` = full run → `outputs = run.outputs` overwrites each loop; carries only last index | Accumulate/merge across baskets or return per-index mapping | medium |
| F-ACTOR-04 | gap | 4 | `tests/test_replay_byte_identical.py:183-193` | byte-identity covers all ActorOutputs incl. `projected_analytics` → field excluded; no test runs `provider=` so path never exercised | Add `provider='TEST'` variant asserting in-memory + parquet byte-identity | low |
| F-ACTOR-05 | gap | 4 | `actor/driver.py:190-247` | lean `run_analytics` pure pass → always builds+discards QcInputs (hidden cost) via delegation | Document overhead or split pure-compute core | low |
| F-ACTOR-06 | drift | 4 | `actor/README.md:10` | document `run_analytics_with_qc`/`AnalyticsRun` (the live entry) → only `run_analytics`/`run_day`/`persist_outputs` documented | Add paragraph for the QC-wired entry | low |
| F-ACTOR-07 | nit | 4 | `actor/driver.py:381` | every param used → `as_of_date` accepted by `_build_qc_inputs`, never referenced | Remove param + call-site arg | low |
| F-ACTOR-08 | nit | 4 | `actor/driver.py:967-977` | single `_is_underlying_key` definition (DRY) → duplicated in driver.py and valuation_join.py | Hoist to `instrument_key.py`, import in both | low |

### ORCHESTRATION (pipeline / run_state / eod / manifest)
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-ORCH-01 | drift | 1 | `orchestration/eod_planning.py:131-137` | "never capture a session that has not closed" → guard is date-granular (`resolved_date > today`); same-day intraday fire admitted even before session_close; session_close resolved but never compared to clock | Reject any index whose `session_close > clock.now()` with labeled skip | medium |
| F-ORCH-03 | gap | 1 | `orchestration/eod_manifest.py:41-44` | manifest distinguishes produced vs none (ADR 0040 #4/#3) → `output_partitions` set for every fired index incl. None-capture; OK status with no on-disk partition | Populate from actual produced partitions / per-index status | low |
| F-ORCH-04 | gap | 1 | `orchestration/eod_stages.py:360-391` | partial multi-basket failure flagged (ADR 0040 #3) → no try/except; one index raises after a sibling committed → raw-but-no-derived, no flag | Per-basket try/except + flagged sentinel + continue | medium |
| F-ORCH-05 | drift | 1 | `orchestration/run_state.py:181-191, 222-227` | latest-by-stage = most recent completion → picks file/append-order winner, not `max(recorded_ts)`; concurrent appenders mis-pick | Break ties by `max(recorded_ts)`, append-order as tiebreak | medium |
| F-ORCH-06 | drift | 4 | `orchestration/pipeline.py:7-13` | ADR 0041 overwrite-rerun → module docstring still "skips… re-does only unfinished tail" | Rewrite to overwrite-by-re-run | low |
| F-ORCH-07 | drift | 4 | `orchestration/pipeline.py:118-203` | ADR 0041 → `run_end_of_day` docstring still ledger-gated skip; truth only in inline comment | Rewrite both docstrings | low |
| F-ORCH-08 | drift | 4 | `orchestration/README.md:19-23,45,83` | ADR 0041 + actual default `_empty_basket_source` → README says skip-tail + "default wiring is collect_live" | Update: re-run all; default is no-capture until 1C | low |
| F-ORCH-09 | nit | 4 | `orchestration/run_state.py:194-213` | post-0041 ledger non-gating → `completed_stages` docstring still "the resume key… pipeline skips" | Reword to observability/last_healthy role | low |
| F-OPS-04 | drift | 4 | `orchestration/pipeline.py:7-13,63-68,109-117` | ADR 0041 → module/EodResult/run_end_of_day docstrings all describe removed skip | Update all three; consider removing/reserving `skipped` | low |

### STORAGE / SNAPSHOTS / AS_OF
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-STORE-01 | blueprint-adr-violation | 1 | `storage/adapter.py:317` | date-range scan excludes `version=<V>/` files (as glob path at 359) → `'version=' not in p.parts` always True; restatements leak into live-only reads (double-count) | Use `if not p.parent.name.startswith('version='):` | low |
| F-STORE-02 | gap | 4 | `storage/adapter.py:315-318` | version-isolation tested on date-range path → `test_read_with_date_range` writes no restatement; bug undetected | Add live+restatement+version=None date-range test | low |
| F-STORE-03 | drift | 4 | `storage/adapter.py:229-360` | one coherent path-resolution decision → 130-line `_partition_files` with inline imports + duplicated provider/version logic across two branches | Extract named helpers, share primitives, hoist imports | medium |
| F-SNAP-01 | gap | 1 | `snapshots/builder.py:199-203` | snapshot `trade_date` from a single coherent session → taken from `used_events[0]` with no all-same-date assertion | Assert single trade_date or take from `context.snapshot_ts` | low |
| F-SNAP-02 | gap | 2 | `snapshots/builder.py:220-226` | `min_open_interest` threaded from versioned config (BP VII) → `assess_quote` called without OI; no field on `QcThresholdConfig`/`qc.yaml` | Add `min_open_interest` to config + thread through | low |
| F-SNAP-03 | nit | 4 | `snapshots/README.md:11` | `config_hashes={...}` dict → example uses scalar `config_hash=h` (raises TypeError) | Fix example to dict form | low |
| F-ASOF-01 | nit | 4 | `snapshots/as_of.py:63-89` | cheap per-instrument as-of read → fresh in-memory DuckDB connect+CREATE+INSERT+close per call | Pure-Python winner (parity-tested) or reuse one connection per batch | medium |

### SURFACES / PRICING / FORWARD
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-SURF-01 | drift | 1 | `surfaces/projection.py:251-253` | each pinned-tenor cell discounts correctly (ADR 0019/0040) → `.get(maturity_years, 1.0)` float-key miss (pinned vs listed-expiry keys) → every cell priced rate-free, no flag | Interpolate DF at pinned tenor from curve, or labeled ProjectionGap on miss | medium |
| F-SURF-02 (merged ×2) | blueprint-adr-violation | 2 | `contracts/tables.py:87-98` | BP IX requires `implied_carry` on ForwardCurvePoint → field absent; computed in ForwardEstimate then discarded; confirmed absent in banked SX5E parquet | Add nullable `implied_carry`, propagate at estimate.py:507-517, regen schema | low |
| F-SURF-03 | nit | 2 | `surfaces/fit.py:40-41` | BP VII fallback_model `spline` → fallback is labeled-linear `nonparametric`; no spline, no `fallback_model` knob | Implement spline or record shipped fallback in ADR | low |
| F-SURF-04 | nit | 2 | `pricing/american.py:37-38` | single documented rho convention (forward-fixed) → American rho bumps r only (q fixed → F moves); European is forward-fixed; conventions split | Bump r+q together, or document per-engine rho split | medium |

### RISK
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-RISK-01 | drift | 1 | `risk/aggregation.py:94-97` | `math.fsum` for reorder-invariance (per attribution.py/scenarios.py) → plain `sum()`; reproducible only via `_by_contract` sort; values differ from fsum on wide books | Import math, replace four `sum()` with `math.fsum()` | low |
| F-RISK-02 | drift | 1 | `risk/scenarios.py:423-430,437-439` | per-underlying/family totals use fsum (like scenario_totals/worst_case) → `+=` accumulation; not bit-stable under reorder | List-collect then `math.fsum` per key; add reorder test | low |
| F-RISK-03 | gap | 1 | `risk/config.py:143,160` | independent lineage stamps (ADR 0028) → tolerance.version and config_version read the same `'version'` key; bumping one silently bumps the other | Distinct keys (`version` / `recon_version`) | low |
| F-RISK-04 | gap | 2 | `risk/multileg.py:196-203` | ADR 0036: every non-None dollar value carries a unit → stock-only basket emits `dollar_gamma=0.0` with `dollar_gamma_unit=None` | Set canonical zero-unit marker for stock legs; assert in test | medium |
| F-RISK-05 | nit | 4 | `risk/scenarios.py:435-450` | documented tie-break → `(total,sid)` picks lexicographically-smallest sid; "worst" claim undocumented as arbitrary | Comment the deterministic-arbitrary tie-break; add tied-PnL test | low |

### QC / VALIDATION
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-QC-02 | gap | 1 | `qc/checks.py:154-186` | zero usable anchor quotes → flag (ADR 0040 #3) → returns PASS (`0.0 <= max_spread_pct`); pinned as intended by test | Return WARN/FAIL/NO_DATA naming missing anchors, or document upstream guarantee | low |
| F-QC-03 | nit | 1 | `qc/checks.py:843` | `measured_value` carries the verdict quantity → magic sentinel `mad_multiplier*1e9` for inf robust-z | Persist via named constant/NaN+reason | low |
| F-TRIAGE-01 | drift | 2 | `validation/triage.py:93-96` | `reason_code` = machine reason for FAIL → stores `'ok'` when `status='fail'` (collides with ForwardEstimate.reason_code); confirmed in banked SX5E parquet | Store QC reason under distinct key (`qc_reason`) | low |

### IBKR / COLLECTORS / CONNECTORS
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-IBKR-01 | drift | 1 | `cp_rest_close_capture.py:183-194` | warm-up returns warmest rows seen (no regress, ADR 0040 #3) → empty re-poll is falsy → `populated=next_populated` regresses; returns last poll | Track/return best (warmest) rows; treat empty as no-progress | medium |
| F-IBKR-02 | drift | 1 | `cp_rest_close_capture.py:339` | qualify nearest-by-maturity expiries → slices `months[:max_expiries]` in raw *listed* order; non-chronological listing drops nearest | Sort option_months chronologically before slicing | low |
| F-IBKR-03 | drift | 5 | `cp_rest_transport.py:24-26` | one reconciled retry/backoff from config → transport literals + collector retry stacked, un-reconciled, never config-driven | Thread IbkrHistoryConfig retry knobs or disable transport retry when collector owns it | low |
| F-IBKR-04 | nit | 2 | `cp_rest_close_capture.py:253-277` | docstring "bid/ask mid" → `_spot_from_snapshot` returns first of last/bid/ask (bare bid, not mid) | Compute mid or correct docstring | low |
| F-IBKR-05 (merged ×2) | nit | 3/1 | `cp_rest_close_capture.py:183-194` | warm-up returns most-populated poll → returns last; flicker-out drops an observed mark | Return maximal-`populated` snapshot | low |
| F-IBKR-06 | nit | 3 | `cp_rest_discovery.py:59` | multiplier from /secdef/info → hardcoded 100; wire field never read | Read multiplier from payload | low |
| F-IBKR-07 | gap | 5 | `cp_rest_transport.py:24-26,59-60,116` | 429/503 budget+base from `ibkr_history.yaml` → `.py` literals; compounds with history retry | Add retry block to YAML; thread into transport at both builders | medium |
| F-IBKR-08 | drift | 1 | `cp_rest_close_capture.py:519-584` | qualified-but-no-marks = explicit empty/flagged (ADR 0040 #3) → returns quote-less IndexBasket; only `event_count=0` in info log | Emit `ibkr.close_capture.no_marks` reason + empty/flagged marker | low |
| F-IBKR-09 | gap | 4 | `cp_rest_close_capture.py:501,575-581` | correlation_id ties capture to EOD run (as history path does) → live close-capture binds only index/as_of | Plumb correlation_id through BasketSource → collect_live_basket | low |
| F-IBKR-10 | nit | 4 | `cp_rest_close_capture.py:13,60,502` | docstring names invoked fn → cites `resolve_index_conid`; code calls `resolve_index` (former unused export) | Fix docstring; demote/note the unused export | low |
| F-IBKR-11 | drift | 1 | `cp_rest_history.py:259-269` | "re-fetches only the missing tail" → `_already_on_disk` true on ANY bar → skips whole ticker; never tail-aware | Correct docstrings (safe) or compute on-disk max trade_date | low |
| F-IBKR-12 | drift | 1 | `cp_rest_history.py:49-51,240-245` | distinguish boundary vs transient 500 (loud-not-silent) → 500 in `_TERMINAL_WINDOW_STATUSES` → treated as "start of history", truncates paging silently | Make 500 retryable; reserve terminal-stop for 404; or distinct WARN | medium |
| F-IBKR-13 | drift | 1 | `cp_rest_close_capture.py:450-459` | every post-close row dropped (docstring) → guard skipped when `_updated` absent/uncoercible; warm-cache rows without `_updated` admitted | Disqualify absent `_updated` in live mode or loud warn | medium |
| F-IBKR-14 | drift | 1 | `cp_rest_normalize.py:53-61` | only live observations feed `last` → `C`-prefix (prior close) stripped to bare float, emitted as `last` at as_of | Capture flag; drop/route `C`-on-`last`; suppress `H` (halted) | medium |
| F-IBKR-15 | nit | 1 | `cp_rest_close_capture.py:185-193` | warm-up continues until warm/stable → `<=` convergence allows a shrinking populated set to count as converged | `populated==requested` or strict-stable non-empty equality | low |
| F-IBKR-16 | nit | 5 | `cp_rest_close_capture.py:186` | injectable sleep (as transport) → `time.sleep` direct; tests pay real seconds | Add injected sleep param threaded from collect_live_basket | low |
| F-CONN-01 | drift | 5 | `infra-deribit/.../deribit_adapter.py:242-245` | subscribe() makes ticks flow under sync `collect_live` (as Saxo does) → `get_event_loop().create_task` on a non-running loop; WS coroutine never runs | Own a thread `asyncio.run(subscribe_ws)`; drop `get_event_loop()` | low |
| F-CONN-02 | drift | 4 | `infra-deribit/.../deribit_discovery.py:82-83` | maturity-window "from config" (docstring) → `min_days=1/max_days=180` literal kwargs; no `deribit.yaml` | Thread from config or correct docstring | low |
| F-CONN-03 | nit | 4 | `infra-deribit/.../deribit_transport.py:49-64` | return type reflects payload → `-> dict` but returns a list for get_instruments | Annotate `-> Any` to match Protocol | low |
| F-CONN-04 | gap | 3 | `infra-deribit/.../deribit_transport.py:49-64` | one consistent httpx transport/backoff → 3 transports, 3 fault shapes, only CpRest retries 429/503 | Shared httpx base (typed error + Retry-After) | low |
| F-CONN-05 | drift | 5 | `infra-saxo/README.md:25-31` | kept leaf wired into spine (ADR 0023) → zero non-test callers; adapters orphaned; README overclaims live participation | Wire a ProviderCapture or soften README to "kept-but-unwired" | low |
| F-CONN-06 | gap | 4 | `infra-saxo/tests/test_real_sample_reconstruct.py:24-52` | real sample exercises adapter parse path → bypasses adapter (codec round-trip only); parsing tested by synthetic dicts only | Add real binary-frame fixture asserting parse_stream/strike_frame | low |
| F-COLLECT-01 | drift | 1 | `collectors/live.py:52-77` | live & replay assign ordinal by SAME canonical rule (ADR 0027) → live stamps in arrival order, replay in `(canonical_ts,event_id)` order; diverge on non-monotonic exchange_ts → re-capture writes NEW events; untested | Buffer-and-sort live by exchange_ts, or assert monotonicity loud; add regression test | medium |
| F-COLLECT-02 | gap | 5 | `collectors/collector.py:147-153` | reload scoped to known trade_date → `_reload_seen_event_ids` full-scans entire raw table, filters in Python | Pass `trade_date=` to `store.read` | low |
| F-COLLECT-03 | gap | 5 | `collectors/collector.py:255-259` | summary scoped to one trade_date → `build_summary` full-scans whole raw layer | Scope read with `trade_date=` | low |

### LOOKAHEAD / MEMBERSHIP / UNIVERSE
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-LOOK-01 | drift | 1 | `infra-ibkr/history_backfill.py:128` | backfill for D resolves constituents known on D → `members()` omits `known_as_of` → NULL → all restatements visible (look-ahead membership) | Pass `known_as_of=as_of_date` | low |
| F-LOOK-02 | gap | 1 | `frontend/routers/constituents.py:92,94` | historical `as_of` resolves knowledge as of then → `members()` omits `known_as_of`; `date.today()` fallback leaks today's basket | Pass `known_as_of=as_of_date` always | low |
| F-UNI-01 | drift | 1 | `universe/calendar_resolver.py:92` | labeled CalendarResolutionError on every failure → Feb-29 `as_of.replace(year-30)` raises bare `ValueError`; crashes leap-day capture/cron | Clamp lookback start / guard Feb-29 | low |
| F-UNI-02 | drift | 2 | `configs/universe.yaml:60-70` | comments describe config as it stands → "UNVERIFIED PLACEHOLDER (conid:0)" block contradicts the verified non-zero conids below it | Replace with "verified live 2026-06-08"; drop done TODO(1C) | low |

### BFF / FRONTEND
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-BFF-01 | drift | 1 | `frontend/serializers.py:218-246` | serve stored one_pct dollar_gamma as-is (post-ADR-0036 engine) → multiplies by 1/100 again → Gamma$ 100× too small; masked by hand-built test fixture | Remove the `/100` rescale; fix test fixture to one_pct convention | low |
| F-BFF-02 | blueprint-adr-violation | 1 | `frontend/routers/health.py:40` | `QcResult.qc_status` (ADR 0029, tables.py:463) → reads `row.status` (nonexistent) → runtime AttributeError for any stored qc_results | `str(row.qc_status).lower()` | low |
| F-BFF-03 | drift | 1 | `frontend/serializers.py:188` | missing cartesian cell = labeled gap (ADR 0019/0040) → `totals.get((s,v), 0.0)` silently fills non-rectangular surface with 0.0 | Use `None` + `has_holes` flag, or reject non-rectangular reshape + warn | low |
| F-BFF-04 | drift | 2 | `frontend/routers/analytics.py:139-142` | `smile.deltas` = signed deltas → fallback (path B) puts `moneyness_bucket` values under `deltas`/`log_moneyness` | Rename key to `moneyness_buckets` + `axis_type` discriminator | medium |
| F-BFF-05 | gap | 3 | `frontend/routers/risk.py:24-25` (+health/price_history/run/config) | REP5: Depends(get_context), response_model, HTTPException → `_context(request)` copy-pasted across ≥5 routers; hand-built JSONResponse 4xx; no response_model; new routers multiply the gap | Land REP5 before more endpoints accrete | medium |

### NOTEBOOKS / SCRIPTS / DEPS
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-NB-01 | blueprint-adr-violation | 3 | `notebooks/apps/_shared.py:107-199` | ADR 0039: `sample_bridge` is the ONLY converter → re-implements `_colon_to_pipe` + `events_to_contracts` field-for-field (second copy that will drift) | Route through `events_to_contracts`/`_colon_to_pipe`; keep only filtering/master-building | medium |
| F-SCRIPT-01 | gap | 5 | `scripts/ohlc_backfill.py:148-157` | loud failed tickers (ADR 0019) → drops `result.failed`; always `return 0`; all-fail sweep exits 0 clean | Log+count failed; non-zero/soft exit when non-empty | low |
| F-SCRIPT-02 | drift | 4 | `scripts/README.md:15-46` | README reflects landed ADR-0039 bridge → says export_sample "Does not write the sample yet" / "no translation layer exists" | Update rows + gap note; keep only reconstruct_sample caveat | low |
| F-SCRIPT-03 | drift | 2 | `scripts/export_sample.py:71-77` | tie-break = snapshot rule `(canonical_ts,event_id)` → strict `>` only; ties pick iteration-order winner; contradicts docstring | Tuple compare `(canonical_ts,event_id)` | low |
| F-SCRIPT-04 | nit | 4 | `scripts/ingest_membership.py:43-47` | anchor to repo root / `ALGOTRADING_DATA_ROOT` → `--store-root` defaults to cwd-relative `./data` | Default to `parents[1]/"data"`, honour env | low |
| F-SCRIPT-05 | nit | 4 | `scripts/eod_run.py:56-61` | docstring matches fail-hard-on-capture (OQ-C) → frames selector as graceful fall-through; omits loud Gateway-down raise | Add fail-hard clause to docstring | low |
| F-DEP-01 | drift | 3 | `packages/infra/pyproject.toml:12-18` | declared dep is imported or dropped (REP0) → `polars`/`pandas` zero imports; comment "the core is polars/..." false | Drop pandas; land or drop polars; fix comment | low |

### EXECUTION / STRATEGY
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-EXEC-01 (merged ×2) | drift | 2 | `pyproject.toml:149-156` | strategy/execution are PEER layers (AGENTS.md:73, map.md:10, READMEs) → import-linter orders execution ABOVE strategy → permits `execution→strategy` | Collapse to one peer line `(algotrading.execution) \| (algotrading.strategy)` | low |
| F-EXEC-02 | drift | 2 | `packages/execution/README.md:3-4` | README `{strategy,execution}` matches enforced contract → README peer-notation vs two-layer pyproject contract | Pick intended direction; align README/pyproject/linter | low |
| F-EXEC-03 | nit | 1 | `packages/strategy/__init__.py:1` | flag stubs-as-real → confirmed clean skeletons, honestly labeled | **No action** (recorded verified-clean) | low |
| F-EXEC-04 | nit | 4 | `packages/execution/tests:0` | tests/ wired into testpaths → empty + absent from `testpaths`; future tests silently skipped | Add to testpaths when code lands; or drop empty dirs | low |

### OPERATIONAL ROBUSTNESS / RETENTION / ALERTS
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-OPS-01 | gap | 5 | `infra-ibkr/live_capture.py:93,156` | CpRestSession keepalive tickled every ~60s → session unpacked to `_session` and `.start()` never called; dense ESTX50 discovery (5–10 min) silently expires session → stale/empty data | Call `_session.start()`/`.stop()` around capture | low |
| F-OPS-02 | gap | 5 | `orchestration/alerts.py:87-227` | evaluated Alerts routed to a channel → 5 alert conditions evaluated+tested but never called in any production path; ALARM lines go to stdout/tmp only; no Telegram/email/webhook | Wire into QC post-run + watchdog; dispatch to operator channel | low |
| F-OPS-03 | gap | 5 | `scripts/gateway_keepalive.py:44-55` (+eod_babysitter.py) | keepalive via CpRestTransport (429 backoff) → bare `httpx.Client`; `_post()` swallows exceptions; silent rate-limit risk post-capture | Min: log HTTP status on non-2xx; consider routing through transport | low |
| F-OPS-05 | gap | 5 | `packages/infra/storage` (N/A) | ADR 0034 cold-compaction by measured threshold → zero implementation; no compact()/prune(); raw growth unbounded | Tracked task: measured-threshold Parquet merge for cold partitions | low |

### CONFORMITY (banked-data schema)
| id | sev | dim | file:line | expected → actual | fix | break_risk |
|---|---|---|---|---|---|---|
| F-CONF-01 | drift | 2 | `infra-ibkr/.../cp_rest_close_capture.py:246` | `raw_broker_payload` kept verbatim (BP I/IX) → hardcoded `"{}"`; all 985 instrument_master rows have empty payload | Thread raw IBKR JSON string into `_master()` | low |
| F-CONF-02 | drift | 2 | `data/.../2026-05-29/.../AAPL` (parquet) | provenance uses `config_hashes` dict (ADR 0028) → 2026-05-29 carries scalar `config_hash`; can't `validate_stamp()`; schema-divergent from 2026-06-10 | Migrate/re-capture or exclude as pre-ADR-0028 | low |
| F-CONF-03 | drift | 2 | `infra-ibkr/history_backfill.py:77` | `config_hashes` key from ADR-0028 taxonomy → uses non-taxonomy `ibkr_history`; all 426,496 daily_bar rows carry it | Rename key to `broker`/`broker.ibkr_history` | low |

---

## 4. Delete / Refactor Candidates

Findings whose action is **refactor** (no `delete` actions present in the set). Sorted by **(value high, break_risk low)**. `break_risk` is the prominent decision column.

| id | action | value | **break_risk** | file:line | what to refactor |
|---|---|---|---|---|---|
| F-IBKR-06 | refactor | high | **LOW** | `cp_rest_discovery.py:59` | Read option multiplier from /secdef/info instead of hardcoded 100 (correctness latent for non-100 names) |
| F-IBKR-03 | refactor | high | **LOW** | `cp_rest_transport.py:24-26` | Reconcile the two stacked retry/backoff mechanisms into one config-driven policy |
| F-COLLECT-02 | refactor | high | **LOW** | `collectors/collector.py:147-153` | Scope `_reload_seen_event_ids` read to `trade_date` (kills unbounded full-table scan on every restart) |
| F-COLLECT-03 | refactor | high | **LOW** | `collectors/collector.py:255-259` | Scope `build_summary` read to `trade_date` |
| F-CONN-04 | refactor | med-high | **LOW** | `deribit_transport.py:49-64` | Shared httpx-transport base (typed error + Retry-After retry) across 3 brokers |
| F-QC-03 | refactor | med | **LOW** | `qc/checks.py:843` | Replace `mad_multiplier*1e9` magic sentinel for inf with named constant/NaN+reason |
| F-IBKR-05 | refactor | med | **LOW** | `cp_rest_close_capture.py:183-194` | Return most-populated warm-up poll, not last |
| F-ACTOR-08 | refactor | med | **LOW** | `driver.py:967-977` | Hoist duplicated `_is_underlying_key` to shared module |
| F-ASOF-01 | refactor | med | **MEDIUM** | `snapshots/as_of.py:63-89` | Pure-Python (parity-tested) winner or reuse one DuckDB connection per batch |
| F-STORE-03 | refactor | med | **MEDIUM** | `storage/adapter.py:229-360` | Extract date-range/glob branches into named helpers; hoist inline imports |
| F-IBKR-07 | refactor | high | **MEDIUM** | `cp_rest_transport.py:24-26,116` | Move 429/503 budget+base into `ibkr_history.yaml`; thread at both builders |
| F-NB-01 | refactor | high | **MEDIUM** | `notebooks/apps/_shared.py:107-199` | Route notebook helper through `sample_bridge` (delete ~30 LOC duplicated converter, re-anchor on ADR-0039 single bridge) |
| F-BFF-05 | refactor | high | **MEDIUM** | `frontend/routers/risk.py:24-25` (+more) | Land REP5: `Depends(get_context)`, response_model, HTTPException — before more routers accrete the hand-rolled style |
| F-ORCH-02 | refactor* | high | **MEDIUM** | `eod_stages.py:360-391` | (also tagged fix) accumulate `outputs` across baskets instead of last-wins overwrite |
| F-ACTOR-01 | refactor* | **critical** | **HIGH** | `driver.py:1006-1009` | (also tagged fix) replace `if not records: continue` with EMPTY/flagged marker — touches the hot persistence path for all 9 tables |
| F-EXEC-03 | refactor-side | n/a | **MEDIUM** | `eod_stages.py:353-391` | `_analytics` mutates closed-over `grid_cells`/`analytics_results` side-channels read by `_qc`; couples stage correctness to run-order — return products explicitly |

\* Items reported under both `fix` and `refactor` framings; listed here because the structural fix is a refactor.

**Reading guidance:** everything above the F-ASOF-01 row is **low-break-risk and safe to batch**. The two **HIGH** rows (F-ACTOR-01, F-EXEC-03/eod_stages closure) and the surrounding **MEDIUM** rows touch shared persistence/closure state and should be done singly with the existing byte-identity tests green (and F-ACTOR-04 added first).

---

## 5. Recommended Dispositions

*No code was changed by this audit. The following routes the 90 distinct findings.*

### A. Should become (or amend) an ADR
- **ADR 0040 enforcement amendment** — driven by **F-ACTOR-01** (×7), F-ACTOR-02, F-ACTOR-03, F-IBKR-08, F-ORCH-03, F-ORCH-04, F-QC-02, F-BFF-03. The invariant is written but unenforced on the live path. The ADR should ratify the **OQ-C ruling** (capture path: fail-hard on raw-but-no-derived; replay path: EMPTY/MISSING marker) so the fix has one authoritative shape.
- **ADR 0028 amendment** — **F-CORE-01 / F-QC-01 / F-CORE-02**: the load path silently defaults economic blocks, contradicting the ADR's own docstrings. Rule explicitly: either enforce nested presence or document the defaults — do not leave the contract aspirational.
- **ADR 0027 clarification** — **F-COLLECT-01**: define whether the exactly-once/byte-identity guarantee requires monotonic exchange_ts per (instrument,field), or whether the live boundary must buffer-and-sort. This is a correctness boundary, not a code tweak.
- **ADR 0034** — **F-OPS-05**: already an ADR; this is a *task* (below), not a new ADR. Record the deferral status.
- **ADR 0039 reinforcement** — **F-NB-01**: the "ONLY converter" rule has a second copy; note in the ADR that the notebook helper must route through the bridge.

### B. Should become tasks / REP rows
- **REP5** (F-BFF-05) — un-landed and spreading; promote to an explicit REP row, coordinate wire shape with `web/src/test/fixtures.ts`.
- **REP0** (F-DEP-01) — close out: drop pandas, land-or-drop polars, fix the comment.
- **Task: ADR 0034 cold-compaction** (F-OPS-05) — tracked, before raw layer grows unwieldy.
- **Task: alert routing** (F-OPS-02) — wire evaluated Alerts to Telegram/email; matches the deferred-disconnect-alert memory note.
- **Task: session keepalive on OAuth path** (F-OPS-01).
- **Task: 429 retry config threading** (F-IBKR-03, F-IBKR-07, F-CONN-04, F-OPS-03) — bundle the four transport/backoff items into one connectivity-hardening task.
- **Task: collector read-scoping** (F-COLLECT-02/03) — small, high-value performance task.
- **Task: lookahead-membership** (F-LOOK-01, F-LOOK-02) — pass `known_as_of`; survivorship-bias correctness, group together.
- **Task: eod_stages refactor** (F-ORCH-02, F-EXEC-03-closure, F-ORCH-04) — the closure side-channel + last-wins outputs + partial-failure flagging are one coherent rework of that stage.

### C. Quick, safe fixes (low break_risk, batchable now)
F-BFF-02 (`qc_status` — runtime crash, do first), F-STORE-01 (one-line version-leak), F-BFF-01 (remove double `/100`), F-RISK-01 / F-RISK-02 (`math.fsum`), F-RISK-03 (split version keys), F-UNI-01 (leap-day clamp), F-IBKR-02 (chronological expiry sort), F-SCRIPT-01 (loud failed tickers), F-SCRIPT-03 (tie-break), F-ACTOR-07 (dead param), F-SNAP-03 (README keyword), F-IBKR-16 (injected sleep). Pair each value-fix with the test the finding names (e.g. F-STORE-01 ↔ F-STORE-02, F-BFF-01 ↔ its fixture, F-ACTOR-01 ↔ F-ACTOR-04).

### D. Documentation-only fixes (drift, no behaviour change)
ADR-0041 doc sweep (F-ORCH-06/07/08/09, F-OPS-04 — do as **one** commit), F-SCRIPT-02 (bridge landed), F-CORE-02/03, F-ACTOR-06, F-CONN-02/05, F-CONN-06, F-SURF-03, F-SURF-04, F-IBKR-04/10/11, F-UNI-02, F-EXEC-02, F-SCRIPT-05, F-CONF-01/02/03 (banked-data conformity — fix the *producer* code for new captures; document/migrate the divergent 2026-05-29 day).

### E. Leave alone (verified-clean or correctly deferred)
- **F-EXEC-03** (strategy/execution skeletons honestly labeled) — recorded verified-clean, **no action**.
- **F-EXEC-04** (empty tests dirs) — **no action** until code lands; optionally drop dirs.
- **F-ACTOR-05** (run_analytics QcInputs overhead) — behaviour not wrong; document only if a perf-sensitive caller appears.
- **F-OPS-03 / F-OPS-05** practical-risk caveats — both findings self-assess low real-world urgency; keep as tracked tasks, do not block the run.

**Sequencing recommendation:** land Section C quick-fixes first (one crash, one double-count, one 100×-wrong number among them), then the F-ACTOR-01 ADR-0040 enforcement (with F-ACTOR-04 test first), then the doc sweep (Section D), then the bundled connectivity and eod_stages tasks. The codebase is healthy enough to ship; the ADR-0040 live-path gap and the three quick correctness bugs (F-BFF-02, F-STORE-01, F-BFF-01) are the items that should not wait.