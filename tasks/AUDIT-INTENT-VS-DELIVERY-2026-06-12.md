# AUDIT — Intent vs Delivered-on-Real-Data (2026-06-12)

> **Read-only audit.** Findings only — no code/config touched. Failure class hunted
> (per `tasks/T-intent-vs-delivery-audit.md`): *config/policy expresses economic intent,
> a separate technical bound/count/default/threshold silently overrides or clips it, and
> the gate stays green because tests check the mechanism, not the delivered economic
> outcome on real data.*
>
> **Oracle used:** the 2026-06-11 captured partitions + QC results already on disk
> (`data/`), read directly with pandas. The live CP gateway was not probed (the capture
> path is a moving target; an agent is landing `T-delta-window`). The 2026-06-11 partition
> was written **2026-06-11 22:47**, which **predates the tenor fix `74d2cc7` (2026-06-12
> 10:18)** — so the on-disk data shows the PRE-fix manifestation of seeds #1/#2. That is
> exactly why it is a clean oracle for "what the green gate delivered." Config-level
> questions (does the YAML still encode wrong intent, independent of any code fix) are
> answered separately in Lane 0.
>
> **Coverage caveat (not silent):** Risk/scenario delivered-data was sampled lightly — no
> `scenario_results`/`risk_aggregates` partition exists for 2026-06-11 on disk, so Lane 4
> is config-and-code only, NOT verified on real output. Saxo/Deribit configs are read in
> Lane 0 but their pipelines produced no 2026-06-11 data (IBKR-only capture), so they are
> config-drift findings only. Front-end (`apps/frontend/web`) is out of scope here.

---

## LANE 0 — CONFIG DRIFT (value-as-written vs value-prescribed)

Prescribing sources: **BP** = `documentation/blueprint/` (absolute reference, ADR 0011);
**TR** = `documentation/transcripts/AlgoTradingCourse2-*.txt` (course intent). Blueprint
`07-configuration.md` values are explicitly *illustrative* — flagged as DRIFT only on a
large/economically-meaningful divergence, else noted as soft-match.

### configs/universe.yaml

| key | config value | prescribed | verdict | note |
|---|---|---|---|---|
| `tenor_grid` (l.37) | `[10d,1m,3m,6m,12m,18m,2y,3y]` | BP 09 l.15 identical; TR l.37 "10[d],1m,3m,[6]m,12m,18m,2y,3y" | **MATCH** | Authoritative copy in BP 09; test pins ordered equality. Config is correct — the bug is downstream (see Cap-1). |
| `strike_selection.delta_bound` (l.29) | `0.30` | TR l.43-44 "-30 DELTA à la monnaie jusqu'à +30"; qc.yaml band ±0.30 | **MATCH** | Correct value; not enforced at capture (see Cap-2). |
| `strike_selection.delta_convention` (l.30) | `forward_undiscounted` | BP (carry==0 ⇒ spot=forward delta) | **MATCH** | Reasonable. |
| `strike_selection.min_strikes_per_side` (l.31) | `2` | not prescribed | NOT-IN-SOURCE | A labeled floor; benign. |
| `underlyings` (l.10) | `[AAPL, MSFT, SPY]` | TR: "OSNP 500 … 504" (S&P 500 ~500 + EuroStoxx 50); top-10 constituents for ATM strategies | **DRIFT (HIGH)** | The **active equity universe is 3 US single-names/ETF**, not SP500+SX5E. The *index* registry (l.71-88, SX5E+SPX) is what actually captured 06-11. `underlyings` is a stale parallel list that no longer matches intent OR the live capture path — a coherence smell (two universe sources, ref. memory "index-addition coherence"). |
| `indices.SX5E/SPX` (l.71-88) | SX5E(ESTX50)+SPX, enabled | TR: EuroStoxx 50 + S&P 500 | **MATCH** | Indices correct; constituents handled elsewhere (SSGA seed). |
| no key | — | TR: "ATM strategies sur les 10 premières [constituents]"; SP500 ~504 constituents | **NOT-IN-CONFIG** | No constituent-count / top-10 selection parameter anywhere in config. Lives implicitly in `data/reference/index_constituents` + SSGA seed. Flag: the "top-10 ATM" policy has no typed home (ADR-0028 gap). |

### packages/infra-ibkr/configs/capture.yaml  ← **strongest Lane-0 drift**

| key | config value | prescribed | verdict | note |
|---|---|---|---|---|
| `collection.n_expiries` (l.9) | `4` | BP/TR term structure to **3y** (8 tenors) | **DRIFT (HIGH)** | 4 nearest expiries cannot span a `10d…3y` grid. Contradicts `universe.yaml tenor_grid` (8). |
| `collection.min_days` (l.11) | `25` | grid needs `10d` (10 days) | **DRIFT (HIGH)** | `min_days:25` **excludes the 10d tenor entirely** — the grid's first point is structurally unreachable from this config. |
| `collection.max_days` (l.12) | `90` | grid needs up to `3y`=1095d | **DRIFT (CRITICAL)** | `max_days:90` clips the capture horizon to ~3 months. The 6m/12m/18m/2y/3y tenors are **impossible** under this config. This is the config-level twin of the tenor bug. |
| `collection.max_strikes_per_session` (l.8) | `90` | — | NOT-IN-SOURCE | Pacing cap. |
| `collection.strike_window_mode` (l.10) | `atm` | TR "toujours l'ATM" centre | MATCH(soft) | Centre is fine; the *width* is the problem (Cap-2). |
| **Whole file** | — | — | **ORPHAN (HIGH)** | The CP-REST close-capture path that produced the 06-11 data does **not read `capture.yaml`'s `collection:` block** (it builds `ChainSelection` from `universe.yaml` via `_selection_from_config`, `cp_rest_close_capture.py:676`). So this file's drifted values are dead for the live path but actively misleading: a reader (or the legacy streaming collector) would honor `n_expiries:4 / max_days:90` and silently clip the grid. **Two capture configs disagree about the term structure.** |

### configs/scenarios.yaml

| key | config value | prescribed (TR l.117-120) | verdict | note |
|---|---|---|---|---|
| `scenario.stress_surface.spot_shock_abs` (l.16) | `0.50` | "moins 50% plus 50% de l'espace" (±50% spot) | **MATCH** | |
| `scenario.stress_surface.vol_shock_abs` (l.17) | `0.50` | "moins 50% plus 50% de la vol" (±50 vol pts) | **MATCH** | |
| `scenario.spot_shocks` (l.11) | `[-0.10,-0.05,0,0.05,0.10]` | families grid (TR: spot ±) | MATCH(soft) | Families grid ≠ the ±50% stress surface; both intended. |
| `scenario.vol_shocks` (l.12) | `[-0.05,0,0.05]` | — | MATCH(soft) | |
| rate shock | **absent** | TR l.119 "moins de 10% de [taux]" (rate ±~10%) | **NOT-IN-CONFIG (MED)** | The course prescribes a **rate shock axis (~±10%)**. `scenarios.yaml` has spot+vol+roll-down but **no rate shock grid**. The risk surface is missing the rate dimension the course asked for. |
| `scenario.stress_surface.spot_steps/vol_steps` (l.18-19) | `9`/`9` | "odd → samples 0" | MATCH | |
| `monetization.gamma_normalisation` (l.23) | `one_pct` | OQ-1/ADR 0036 | MATCH | per-1%-move Γ. |
| `monetization.theta_day_count` (l.25) | `365` | ADR 0036 | MATCH | |

### configs/qc.yaml

| key | config value | prescribed (BP 07 illustrative) | verdict | note |
|---|---|---|---|---|
| `max_spread_pct` (l.9) | `0.05` | BP `0.25` | **DRIFT (note)** | 5% vs illustrative 25%. Tighter is defensible; flag as intentional-tightening, verify owner intent. |
| `max_quote_age_seconds` (l.10) | `30.0` | BP `60` | DRIFT(soft) | Tighter; benign. |
| `min_chain_count` (l.11) | `6` | BP `min_points_per_slice: 5` | MATCH(soft) | Close to 5; OK. |
| `grid.tenor_floors.*` (l.23-30) | all `5` | — | NOT-IN-SOURCE | Matches BP min_points 5; reasonable. |
| `grid.band_low/high_delta` (l.33-34) | `-0.30/0.30` | TR ±30Δ | **MATCH** | |
| `grid.max_delta_step` (l.37) | `0.25` | — | NOT-IN-SOURCE | A 0.25 delta gap is *large* (half the band); a loose completeness bar (see QC-3). |
| `forward_engine.max_residual_mad` (l.51) | `0.05` | — | **DRIFT (HIGH — units)** | Absolute price units; on a 7400-pt index this is ~7e-6 relative — unmeetable (see An-2/QC-4). |
| `forward_engine.max_parity_residual` (l.53) | `0.10` | — | **DRIFT (HIGH — units)** | Same: absolute $ on a 7400-pt index. |
| `forward_engine.min_forward_confidence` (l.52) | `0.5` | — | NOT-IN-SOURCE | OK. |
| `fit_tolerance.max_surface_rmse` (l.60) | `0.02` | BP `max_rmse: 0.02` | **MATCH** | But blind to arb (QC-2). |
| `fit_tolerance.max_non_convergence_ratio` (l.59) | `0.10` | — | NOT-IN-SOURCE | OK. |
| `anomaly.*` (l.67-70) | mad 5.0 / warn_z 3.5 / fail_z 5.0 / min_baseline 10 | BP `max_robust_zscore 3.5` | MATCH(soft) | warn_z matches BP 3.5. |
| `continuity.*` (l.43-45) | gap 5/1, coverage 0.95 | — | NOT-IN-SOURCE | OK. |

### configs/pricing.yaml

| key | config value | prescribed (BP 07) | verdict | note |
|---|---|---|---|---|
| `solver.iv_tolerance` (l.11) | `1.0e-8` | BP `price_tolerance 1.0e-6` | DRIFT(soft) | Tighter; benign (and a different quantity — vol-tol vs price-tol). |
| `solver.max_iterations` (l.12) | `100` | BP `100` | **MATCH** | |
| `solver.vol_min` (l.13) | `1.0e-9` | BP `lower_vol 1e-4` | DRIFT(note) | 1e-9 vs 1e-4 floor; very low floor (harmless bracket). |
| `solver.vol_max` (l.14) | `5.0` | BP `upper_vol 5.0` | **MATCH** | |
| `surface.svi_*_bounds` (l.18-22) | a[0,10] b[1e-8,10] rho[-0.999,0.999] m[-5,5] sigma[1e-8,10] | BP svi/spline (no explicit bounds) | NOT-IN-SOURCE | rho bound ±0.999 is the rail that seed #3 hits on real data (An-3). |
| `surface.svi_max_iterations` (l.24) | `200` | — | NOT-IN-SOURCE | OK. |
| `forward.good_rel_residual` (l.28) | `1.0e-3` | — | NOT-IN-SOURCE | **Relative** residual basis — inconsistent with QC's absolute `max_residual_mad` (An-1). |
| `forward.fair_rel_residual` (l.29) | `1.0e-2` | — | NOT-IN-SOURCE | same. |
| `surface fallback_model: spline` | **absent** | BP `fallback_model: spline`, `model: svi` | **NOT-IN-CONFIG (MED)** | BP prescribes an SVI model with a **spline fallback** and `min_points_per_slice: 5`. `pricing.yaml surface:` block has SVI bounds but **no `model`/`fallback_model`/`min_points_per_slice` keys** — the model choice + fallback policy live as `.py` literals, an ADR-0028 gap. |

### packages/infra-ibkr/configs/ibkr_history.yaml — checked, all operational (not economic), no drift.
### configs/environment.yaml, configs/broker.yaml — checked, operational-only, no economic drift.
### packages/infra-deribit/configs/*.yaml

| key | value | prescribed | verdict |
|---|---|---|---|
| `forwards_deribit.forward_engine.max_candidate_count` (l.17) | `12` | BP 07 `max_candidate_count: 12` | **MATCH** |
| `forwards_deribit.outlier_method/max_robust_zscore` (l.18-19) | `mad`/`3.5` | BP `mad`/`3.5` | **MATCH** |
| `forwards_deribit.strike_band_mode` (l.16) | `nearest_liquid` | BP `nearest_liquid` | **MATCH** |
| `forwards_deribit.min/max_rate` (l.24-25) | `-0.10`/`0.60` | crypto-funding rationale documented | MATCH(domain) |
| `universe_deribit` BTC/ETH | — | not in course scope (course = SP500/SX5E) | NOT-IN-SCOPE |

> **Note:** the *equity* `forward_engine` block (BP 07: max_candidate_count/outlier_method/
> max_robust_zscore) has **no equivalent in `configs/pricing.yaml`** — only Deribit/Saxo carry
> it. The equity forward engine's candidate cap + outlier policy thus live as code defaults
> (ADR-0028 gap) — see An-4.

### packages/infra-saxo/configs/*.yaml — checked; rate/dividend bounds plausible, SPX excluded by entitlement (documented). No 2026-06-11 Saxo data; config-drift only, no economic conflict with course intent (Saxo is a secondary provider).

---

## LANES 1-5 — INTENT vs DELIVERED-ON-REAL-DATA

### Area: CAPTURE / SELECTION

| # | file:line | intended policy (source) | the clip / override | why tests stay green | QC catches? | sev | remediation task |
|---|---|---|---|---|---|---|---|
| **Cap-1** | data oracle: `data/raw/raw_market_events/trade_date=2026-06-11` SPX expiries = `2026-06-10…2026-06-22` (all ≤11d); `surface_parameters` maturities `[0.0137,0.0164,0.0192,0.0301]y`. Config: `universe.yaml:37` grid `10d…3y`; `capture.yaml:9-12` n_expiries=4/min_days=25/max_days=90 | Capture must span the pinned `10d…3y` term structure | **(pre-fix)** nearest-N expiry selection kept the front cluster; `capture.yaml max_days:90` would in any case clip to ≤3m. Broker lists SPX→2031, SX5E→2035 (memory "tenor-selection-bug") so the data exists | tests asserted nearest-N worked, never "spans the grid"; golden fixtures hand-built short slices | **YES — `tenor_coverage_floor` FAIL, measured −5.0** (count−floor, i.e. tenors at 0 vs floor 5) — unread until this audit | **CRITICAL** | T-tenor-selection (fixed `74d2cc7`; **verify post-fix recapture actually lands 1m…3y, and FIX/RETIRE capture.yaml max_days:90**) |
| **Cap-2** | data oracle: SPX 32 strikes spanning **7315–7470** (±~1.05% around spot ~7400); `projected_option_analytics` delivered `delta` ∈ {±0.50} only (ATM). Clip: `cp_rest_close_capture.py:101 _DISCOVERY_STRIKES_PER_SIDE = 16`. Intent: `universe.yaml:29 delta_bound 0.30` | Strikes must span −30Δ → ATM → +30Δ | A fixed **16-strike-per-side** count qualifies a ±1% block; the docstring (l.92-100) *asserts* it is "a superset of the 30Δ band" but **never verifies it on data** — on a 10d option ±30Δ is far wider than ±1%. The claimed superset is the lie that hid the bug (same shape as seed #2) | tests check the per-side count mechanism, not "covers ±30Δ on real chains" | **YES — `delta_band_completeness` FAIL, measured 8.0** (8 tenors with band gaps) — unread | **CRITICAL** | T-delta-window (spec'd; **make the per-side count derive from / be validated against the 0.30 band on real strike ladders, and assert delivered deltas reach ±0.30**) |
| **Cap-3** | `capture.yaml` orphaned vs live path (Lane 0) | one coherent capture policy | `capture.yaml collection:` (n_expiries/min_days/max_days) is **not read** by close-capture; `universe.yaml` drives it | no test asserts the two configs agree, or that capture.yaml is consumed | no | **HIGH** | T-capture-config-coherence (retire or wire capture.yaml; one source of capture span) |

**Checked & clean (capture):** `min_strikes_per_side` floor is a *labeled* floor (not silent); `max_strikes_per_session:90` pacing cap did not bind (32<90 captured); index conids verified live (universe.yaml:79,86).

### Area: ANALYTICS (forward / parity / surface / Greeks)

| # | file:line | intended policy (source) | clip / override | why tests stay green | QC catches? | sev | remediation |
|---|---|---|---|---|---|---|---|
| **An-1** | label: `forwards/estimate.py:193` `relative_residual = residual_mad/forward`; thresholds `pricing.yaml:28-29 good/fair_rel_residual`. QC: `qc/checks.py:254` `residual_mad > max_residual_mad(0.05)`. Data: 06-11 forward diag `quality_label:'good'` at `residual_mad=0.159` while QC `forward_stability` FAILs 0.159 | self-label and QC must agree on whether a forward is trustworthy | label uses a **relative** residual (0.159/7400≈2e-5 ⇒ "good"); QC uses the **absolute** MAD (0.159 > 0.05 ⇒ FAIL). Two different axes ⇒ "good" forwards fail QC, and the diagnostic mislabels a 3×-over-threshold forward as good | label test feeds relative residuals; QC test feeds absolute — neither crosses the axes | partial: QC `forward_stability` flags it, but the **diagnostic self-label contradicts QC** (the spec's label-vs-threshold inconsistency) | **HIGH** | T-forward-label-threshold-coherence (one residual basis; reconcile rel vs abs) |
| **An-2** | `qc/checks.py:281-324` parity residual vs `qc.yaml:53 max_parity_residual 0.10`; data: SPX parity_residual measured **105.4 / 67.7 / 3.9 / 2.5** vs threshold 0.10 | a parity residual cut that means the same thing across underlyings | residuals are **absolute price points** on the C−P regression; on a 7400-pt index a "good" parity residual is naturally O(1–100) pts, but the threshold `0.10` is an **absolute $** value tuned for small-price equity options. The threshold does not scale with spot ⇒ the check is **unmeetable** on index options (always FAIL) | tests feed hand-built residuals like 0.03/0.30 — pass/fail at the $0.10 scale; never an index-scale chain | **mis-fires (always FAIL on index)** — a false-positive blind spot: the check is "green-when-wrong" inverted (red-always), so operators learn to ignore it | **HIGH** | T-parity-residual-units (express threshold relative to forward / in bps, or per-underlying) |
| **An-3** | `qc/checks.py:370-403 check_surface_fit_error` scores `fit.rmse` only vs `qc.yaml:60 max_surface_rmse 0.02`; data: all 4 SPX slices `surface_fit_error` PASS (rmse~6e-6) while **3/4 carry `arb_free:False`** (slice0 `rho=-0.999` railed, slice2 `sigma=0.0000` degenerate) | a passing surface QC should mean the smile is usable (arb-free, non-degenerate) | the check reads **RMSE only**; `SliceFit` carries `arb_free` + `butterfly_violations` but the check never inspects them (seed #3). An over-fit degenerate slice (rho railed to the `pricing.yaml:20` bound, sigma→0) scores tiny RMSE and PASSES | tests assert RMSE pass/fail on clean fixtures; never assert arb-freeness propagation | **NO** — `surface_fit_error` is the blind check itself | **HIGH** | T-surface-arbfree-qc (fail/warn on arb_free=False or bound-hit; this is seed #3) |
| **An-4** | equity forward engine candidate cap / outlier policy: no `pricing.yaml` keys (Lane 0); BP 07 prescribes `max_candidate_count 12 / outlier mad / max_robust_zscore 3.5` | candidate cap + outlier rejection are economic ⇒ typed config (ADR 0028) | equity values live as `.py` defaults (only Deribit/Saxo carry the block in config). Not a clip per se, but the intent has no config home ⇒ silent default risk | no test asserts the equity engine reads config for these | n/a | **MED** | T-equity-forward-config (add forward_engine block to pricing.yaml) |
| **An-5 (clean)** | Greeks units `projected_option_analytics`: `dollar_gamma_unit="$ per 1% move"`, `dollar_vega_unit="$ per 1 vol point"`, `dollar_theta_unit="$ per calendar day"`, `dollar_rho_unit="$ per 1% rate"` — **match** `scenarios.yaml monetization gamma_normalisation:one_pct, theta_day_count:365` | $-Greek conventions | — | — | no QC asserts the convention, but the delivered units are correct & self-describing | **CLEAN** | (optional) T-greek-unit-qc — no QC asserts monetization convention (blind, but currently correct) |

**Checked & clean (analytics):** IV solver convergence (QC `iv_solver_convergence` PASS, val 0.0); dollar-Greek unit labels are present and correct on the persisted analytics.

### Area: QC (thresholds, blind spots, semantics)

| # | file:line | intended | clip / blind spot | tests green? | catches? | sev | remediation |
|---|---|---|---|---|---|---|---|
| **QC-1** | `actor/driver.py:813` `cells.extend(result.cells)` (gaps dropped); `:1006-1008 persist_outputs … if not records: continue`; data: `projected_option_analytics` has **only 2 rows** (atm/atmp @10d) of a 64-cell (8×8) grid | the grid's missing cells must be visible to the read/QC plane | `project_grid` emits `ProjectionGap` for tenor_beyond_span / no-curve, but **gaps are never persisted** (only `cells`), and `persist_outputs` **silently skips** empty tables with no log (ADR-0040, F-ACTOR-01). The front + QC therefore see "1 tenor, ATM only" and cannot tell 62 cells are missing | persistence tests check non-empty writes; never assert gap-row presence or grid completeness on real (short-span) input | indirectly via `tenor_coverage_floor`/`delta_band_completeness`, but **the analytics table itself hides the holes** | **HIGH** | T-persist-gap-rows / ADR-0040 enforcement (persist gaps or log a coverage delta) |
| **QC-2** | (see An-3) | — | RMSE-only surface check, blind to arb | — | NO | HIGH | (An-3) |
| **QC-3** | `qc.yaml:37 max_delta_step 0.25`; `qc/checks.py:660-735` | the band must be densely covered (the course wants −30/−20/−10/ATM/+10/+20/+30) | a `0.25` max step is **half the full ±0.30 band** — a tenor with only {−0.30,+0.30} (no interior) could nearly pass the step test; the completeness bar is loose relative to the 8-point band the projection builds | tests feed compliant/obviously-broken delta sets | self-consistent but **loose** | **MED** | T-delta-step-tighten (align max_delta_step with the 8-point band density) |
| **QC-4** | (see An-2) | — | absolute parity/residual thresholds don't scale with index spot | — | mis-fires | HIGH | (An-2) |
| **QC-5** | `qc/checks.py` (no monetization/stress-grid validator) | — | no QC asserts $-Greek monetization convention or stress-grid coverage | n/a | NO | LOW/MED | T-monetization-qc, T-scenario-grid-qc |

**Checked & clean (QC):** `tenor_coverage_floor` correctly raises on a pinned tenor missing its floor (never defaults to 0, `thresholds.py:124`); `delta_band_completeness` reads band edges from config not data; `underlying_quote_health` and `option_chain_coverage` PASS on real data and read their configured thresholds; `calendar_sanity` FAILs (val 5.0) consistent with the ultra-short-slice symptom of Cap-1.

### Area: RISK / SCENARIO  *(sampled — see caveat; NO 2026-06-11 risk partition on disk)*

| # | file:line | intended | clip / gap | sev | remediation |
|---|---|---|---|---|---|
| **Rk-1** | `scenarios.yaml` has spot+vol+roll-down, **no rate shock** (Lane 0); TR l.119 prescribes rate ±~10% | stress grid must include a rate axis | the rate-shock dimension is **absent from config** — the delivered stress surface cannot cover the rate moves the course asked for | **MED** | T-scenario-rate-axis |
| **Rk-2 (not verified)** | scenario aggregation / reconciliation tol | — | could not verify on real data — no `scenario_results`/`risk_aggregates` partition exists for 2026-06-11 | n/a | (re-audit after a risk run lands) |

**Explicitly skipped (not silent):** Lane 4 delivered-data verification — no risk output on disk for 06-11. Reconciliation tolerances were not traced to code in this pass.

### Area: STORAGE / CONTRACTS

| # | file:line | intended | gap | sev | remediation |
|---|---|---|---|---|---|
| **St-1** | `actor/driver.py:1006-1008 persist_outputs … if not records: continue` | every (trade_date, underlying) lands symmetrically; empty ≠ silent | empty derived table **silently skipped, unlogged** ⇒ asymmetric partitions (surface without grid; analytics with 2 rows vs 64). Same root as QC-1; ADR-0040 invariant unenforced (F-ACTOR-01, flagged 7× in the 06-11 audit) | **HIGH** | ADR-0040 enforcement (fail-loud / log on empty when upstream non-empty) |

**Checked & clean (storage):** partition layout (`trade_date=…/underlying=…`) is consistent; no version leak observed in the 06-11 partitions read; config hashing scope (universe/qc/pricing/scenarios hashed; environment/broker not) matches the economic-vs-operational rule.

---

## RANKED TOP FINDINGS

1. **Cap-1 (CRITICAL) — Tenor grid not delivered.** On-disk SPX/SX5E capture spans only ≤11 days; `surface_parameters` carries 4 short slices vs the `10d…3y` grid. `tenor_coverage_floor` FAILed (−5.0) and went unread. Code fix landed (`74d2cc7`) **after** this data; **`capture.yaml max_days:90 / min_days:25 / n_expiries:4` still encode the wrong intent** and must be retired/fixed, and a post-fix recapture must be verified to actually reach 1m…3y.
2. **Cap-2 (CRITICAL) — Delta band not delivered.** Captured strikes span ±1.05% (7315–7470); delivered analytics are ATM-only (±0.50Δ). `_DISCOVERY_STRIKES_PER_SIDE=16`'s docstring *claims* a "superset of the 30Δ band" but never verifies it. `delta_band_completeness` FAILed (8.0) and went unread.
3. **An-3 / QC-2 (HIGH) — `surface_fit_error` blind to arbitrage.** All 4 SPX slices PASS on RMSE (~6e-6) while 3/4 are `arb_free:False` (rho railed to −0.999, sigma→0). The QC check scores only RMSE — this is seed #3, confirmed on real data.
4. **QC-1 / St-1 (HIGH) — Projected-grid holes are invisible.** Only 2 of 64 grid cells persisted; `ProjectionGap`s are dropped (`driver.py:813`) and `persist_outputs` silently skips empties (`:1006`). The analytics table hides its own incompleteness (ADR-0040, F-ACTOR-01).
5. **An-1 (HIGH) — Forward quality label vs QC threshold disagree.** Diagnostic labels `residual_mad=0.159` "good" (relative basis) while QC fails it at 0.05 (absolute). Two axes; the self-label is the authoritative-looking lie.
6. **An-2 / QC-4 (HIGH) — Parity/residual thresholds mis-scaled.** Absolute `$0.10 / $0.05` thresholds on a 7400-pt index ⇒ measured residuals 105/67; the check is effectively always-FAIL and trains operators to ignore it.
7. **Lane-0 capture.yaml (HIGH) — Orphaned, drifted capture config.** `n_expiries:4 / max_days:90` contradict the grid and aren't even read by the live path — two disagreeing capture configs.
8. **Lane-0 universe.underlyings (HIGH) — Stale equity universe.** `[AAPL,MSFT,SPY]` no longer matches the SP500+SX5E intent or the live index capture; "top-10 ATM constituents" has no typed config home.
9. **Rk-1 (MED) — Missing rate-shock axis** in `scenarios.yaml` vs the course's rate ±10%.
10. **MED/config-gaps** — `pricing.yaml` missing `model/fallback_model/min_points_per_slice` (BP 07) and the equity `forward_engine` block (An-4); `max_delta_step:0.25` loose (QC-3).

### Method-question summary for the confirmed class-instances
All of Cap-1, Cap-2, An-1, An-2, An-3, QC-1 satisfy the three-part test: (1) intended policy is a typed config value (tenor_grid, delta_bound, max_residual_mad, max_surface_rmse) or a prescribed BP/TR value; (2) a code-side count/window/units/drop silently overrides it (`_DISCOVERY_STRIKES_PER_SIDE`, nearest-N, gap-drop, RMSE-only, absolute-units, relative-vs-absolute label); (3) tests stayed green by checking the mechanism on hand-built fixtures, while the matching QC check either FAILed-and-was-unread (Cap-1, Cap-2) or is itself blind (An-3).
