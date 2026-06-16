# 0052 — QC coverage to the blueprint: interpolate interior tenors, fall back at the edges, no hard per-tenor floor

- **Status:** accepted, 2026-06-16 (owner ruling, Vincent — "follow the blueprint").
- **Date:** 2026-06-16.
- **Implements:** the blueprint as plan-of-record ([[0011-blueprint-as-plan-of-record]]) — Part II
  §"Surface parameterization and no-arbitrage diagnostics" (Eq. 21 calendar monotonicity, Eq. 22
  variance interpolation across maturities), `05-math-notes` (sparse-maturity fallback policy),
  `09-data-dictionary` (`tenor_grid` is a *projection* grid), `14-slos-monitoring` (coverage is a
  ratio over *monitored* maturities), `07-configuration` (`strike_band_mode: nearest_liquid`).
- **Relates to:** ADR 0028 (QC cut-offs are economic, hashed config),
  [[0051-return-to-blueprint-dispersion-realized-vol-diagnostic]] (same class of fix: a layer
  demanding more than the blueprint asks), [[delta-window-fix]], [[tenor-selection-bug]].

## Context

After the ADR-0051 amputation the close capture is index-only and the constituent QC noise is gone
(263 → 15 QC fails on the 2026-06-16 SX5E re-capture). But **SX5E itself still pages three CRITICAL
checks** — `tenor_coverage_floor`, `delta_band_completeness`, `calendar_sanity` — and a forensic
read of the run (`9a3d7ac…`, index-only) shows the failures are **only at the edges of the curve**:

- The **liquid core passes**: `1m, 3m, 6m, 12m, 18m` each carry 26–31 usable points.
- `tenor_coverage_floor` breaches are **`10d` (no expiry that short), `2y`, `3y`** — the Eurex SX5E
  LEAPs, genuinely illiquid / one-sided at the snapshot → 0 usable points.
- `delta_band_completeness` breaches are the same edge tenors (`too_few_points`,
  `low/high_edge_unreached`), plus tiny interior δ-step gaps on the long wings.
- `calendar_sanity` fires CRITICAL on a **~2e-4 total-variance non-monotonicity** at the short end
  (`w_long=1.62e-3` vs `w_short=1.87e-3`) — numerical noise in ultra-short variances.

The current checks (`infra/qc/checks.py`) hardcode `severity=SEVERITY_CRITICAL` and treat **any**
breach of a flat per-tenor floor as a fail. `configs/qc.yaml` sets `tenor_floors: {10d…3y: 5}` — a
hard **5 usable two-sided points at every pinned tenor**, and `band_low/high_delta: ±0.30` with
`max_delta_step: 0.02` demands the **full ±30Δ band at every pinned tenor**, 2y/3y included.

**This contradicts the blueprint.** Three independent statements:

1. **The pinned grid is a *projection* target, filled by interpolation — not a capture floor.**
   `09-data-dictionary`: `tenor_grid` is "the ordered standard maturities analytics **project** the
   surface/Greeks onto". `02-math-framework` Eq. 22: total variance across maturities is
   `w(T) = λ·w(T₁) + (1−λ)·w(T₂)` — an **interior pinned tenor with no direct capture is
   interpolated from its liquid neighbors**, not required to hold its own 5 liquid quotes.
2. **Sparse maturities get an explicit fallback, never a hard fail.** `05-math-notes`: "not every
   maturity is equally informative … a maturity with two sparse strikes and wide spreads should
   produce a **low-confidence** forward … the fallback policy should be explicit: **interpolate from
   neighboring maturities, borrow the previous trusted snapshot, or mark the maturity unusable**."
   The 2y/3y LEAPs and the sub-front 10d are exactly this case.
3. **Coverage is a ratio, and calendar/arb checks are *basic* diagnostics for *gross* pathologies.**
   `14-slos-monitoring`: maturity coverage is a "**> 95% of monitored maturities**" ratio, on the
   maturities actually monitored — not 100% at every projected pin. `02-math-framework`: the system
   tests "**at minimum for obvious calendar inconsistencies and gross cross-strike pathologies**…
   advanced no-arbitrage enforcement can be added in later releases." `qc.yaml` itself flags this
   ("teeth to retune once a real term structure is banked; ultra-short calendar noise is a separate
   symptom"). A 2e-4 wiggle is neither obvious nor gross.

The result is a **false-critical**: SX5E is captured correctly across its liquid range, but the QC
pages the whole basket over illiquidity the blueprint explicitly anticipates and prescribes
fallbacks for. It is the ADR-0051 pattern again — a layer (here QC) demanding more than the
blueprint asks.

## Decision

**Recalibrate the coverage / arb QC to the blueprint. Coverage is judged over the liquid range with
interpolation and explicit fallback at the edges; only gross pathologies page CRITICAL.**

1. **Interior pinned tenors without direct capture are interpolated, not failed (Eq. 22).** A pinned
   tenor that lies **between** two liquid monitored maturities is filled by total-variance
   interpolation; it does not need its own ≥5 captured points. `tenor_coverage_floor` is evaluated
   on the surface AFTER interpolation, so an interpolatable interior gap is not a breach.
2. **Edge pinned tenors below/above the liquid range are a labelled fallback, not CRITICAL.** A
   pinned tenor that requires **extrapolation** beyond the liquid range (e.g. `2y`/`3y` when the
   longest liquid maturity is `18m`, or `10d` below the front expiry) is **marked low-confidence /
   unusable** (`05-math-notes`) and emitted as a **WARNING**, not a CRITICAL. The surface stays
   interrogable; the operator sees the honest gap.
3. **Coverage becomes a ratio over the liquid/monitored range (`14-slos`: ≥ 95%), not a hard
   per-tenor floor.** The per-tenor minimum-points idea survives only as a **within-liquid-range**
   floor; the pinned-grid completeness is a coverage ratio with a documented floor.
4. **`delta_band_completeness` follows the same interior/edge split.** Full ±30Δ band is required
   only where the surface is liquid; on extrapolated edge tenors a partial band is a warning, not a
   critical. Strike selection stays `nearest_liquid` (`07-config`).
5. **`calendar_sanity` pages CRITICAL only on a *material* (gross) inversion.** Add an absolute /
   relative variance tolerance and an ultra-short-maturity guard so numerical noise (≲ a few e-4 of
   total variance, or maturities below a short floor) is at most a WARNING. Eq. 21 stays the
   diagnostic; "gross" is the bar (`02-math-framework`).

`tenor_floors`, the band edges, `max_delta_step`, the coverage ratio, the calendar tolerance and the
ultra-short floor are **economic, hashed config** (`configs/qc.yaml`, ADR 0028) — regenerate the
`qc` config-hash golden when they move. **No capture path changes**: this is purely the
derived-surface QC verdict. The index close-capture and the ADR-0051 index-only scope are untouched.

## Consequences

- SX5E (and any index) stops paging CRITICAL for illiquid LEAP / sub-front tenors the blueprint
  expects to interpolate-or-mark-unusable; the close capture's QC verdict becomes trustworthy again
  (a CRITICAL means a real defect, not expected edge illiquidity).
- The surface gains the blueprint's Eq.-22 interior interpolation and `05-math-notes` edge fallback
  as first-class, labelled behaviours (the low-confidence/unusable mark is auditable).
- Risk: tuned too loose, a genuine coverage collapse could read as a warning. Mitigation: the
  within-liquid-range floor and the ≥95% monitored-coverage ratio keep teeth on the part of the
  curve that should be liquid; only the structurally-illiquid edges degrade to warnings.

## Out of scope

Advanced no-arbitrage enforcement (full butterfly/calendar surface repair) stays a later release
(`02-math-framework`). This ADR only realigns the *basic* diagnostics and the coverage verdict to
the blueprint; it does not add new arb machinery.
