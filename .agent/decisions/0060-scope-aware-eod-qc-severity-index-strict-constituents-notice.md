# 0060 — Scope-aware EOD QC severity: the index stays strict, constituents are notice-only

- **Status:** accepted, 2026-06-19 (owner ruling, Matthieu).
- **Date:** 2026-06-19.
- **Relates to:** [[0059-re-enable-full-membership-constituent-option-capture]] (this is the QC
  consequence of that capture decision — full-membership constituent capture is what put ~44 noisy
  single-name surfaces in front of the close QC), [[0052-qc-coverage-floors-to-blueprint-interpolate-and-fallback]]
  (the same class of fix: don't let a calibrated-for-the-index gate page on data it was never
  calibrated for; that ADR re-tuned the gates *for the index*, this one re-scopes them *by
  underlying*), [[0028-economic-config-hashed]] (QC severity-by-scope is engine logic, not a new
  threshold, so no config-hash move).

## Context

ADR 0059 re-enabled full-membership constituent option capture: every EOD close now banks the index
option surface AND the option surface of every SX5E constituent that carries a weight (~44 names).
That is deliberately the maximum-data posture the owner asked for.

The QC plane was built and calibrated for the **one tradeable index** (SX5E). Its CRITICAL gates —
`calendar_sanity`, `tenor_coverage_floor`, `delta_band_completeness`, `greek_sanity`,
`scenario_completeness`, `underlying_quote_health`, `put_call_iv_spread`, `collector_continuity` —
each hardcode `SEVERITY_CRITICAL`, and a CRITICAL fail PAGES (`escalation_level` → PAGE) and blocks
the trade date from banking "healthy". Those thresholds are correct for SX5E, where the surface is
liquid and a CRITICAL fail is a real defect a PM must act on.

Applied unchanged to ~44 illiquid single-name constituent surfaces, the same gates fire constantly:
single-name EUREX option chains are thin, one-sided, and gappy by nature, so a constituent trips
`tenor_coverage_floor` / `delta_band_completeness` / `calendar_sanity` on essentially every close.
The result is that EOD QC PAGES every night and the date is blocked from banking healthy — over data
that is *expected* to be noisy and that the desk does not trade. This is the ADR-0052 pattern again:
a gate demanding of the constituents what only the index can deliver.

The index is the only thing traded; the constituents are captured purely as a data surface (ADR 0059
is explicit they are not even an input to the ρ̄ diagnostic). So a constituent's surface defect is
information, not an incident.

## Decision

**Make EOD QC severity scope-aware. The index stays strictly CRITICAL (unchanged). On a constituent,
a CRITICAL-gate failure is downgraded to notice-level so it never pages and never blocks banking.**

1. **`is_index: bool = True` on the CRITICAL-gate checks.** Each of the eight CRITICAL gates in
   `infra/qc/checks.py` takes an `is_index` keyword, defaulting to `True`. A single helper
   (`_scope_critical`) downgrades a would-be CRITICAL **fail** to a **WARNING** when
   `is_index=False`, in BOTH dimensions:
   - `severity` CRITICAL → WARNING, so `escalation_level` (unchanged) yields NOTICE, not PAGE.
   - `qc_status` FAIL → WARN, so the report's worst-of `overall_status` is at worst WARN, not FAIL,
     on a constituent-only failure.
   The downgrade only ever touches a severity-CRITICAL FAIL. A WARNING or PASS the check already
   produced is returned untouched regardless of scope, and `is_index=True` is a no-op — so every
   pre-existing caller (default `True`) keeps the exact strict behaviour.

2. **Scope is threaded from orchestration, where the index is known.** A basket run mixes the index
   and its constituents into one `QcInputs`, so scope is decided per-check by the check's own
   underlying: `analytics_qc_results(..., index_symbol=...)` and `run_qc(..., index_symbols=...)`
   set `is_index = (underlying == index_symbol)` per result. The orchestration call sites pass the
   fired index symbol(s) (`fired_index.entry.symbol` / the basket keys). `underlying_quote_health`
   spans the whole captured batch (the index anchor included) and stays index-strict by design — a
   dead index anchor must still page. `scenario_completeness` is portfolio-wide on the index book
   and stays strict. When no index symbol is supplied (the default for all existing callers and
   unit tests), everyone is strict, preserving current behaviour.

3. **Banking keys off the PAGING escalation, not the raw `overall_status`.** Previously the QC stage
   banked the date healthy only when `overall_status == "pass"`. A downgraded constituent failure is
   `overall_status == "warn"`, which under the old rule would still block banking. The QC stage now
   banks healthy unless the escalation PAGES (`escalation != ESCALATION_PAGE`). A genuinely-blocking
   QC failure is exactly one that pages — a CRITICAL-severity fail, which post-this-ADR means an
   *index* defect. `escalation_level` and the report aggregation are **unchanged**; only the
   `pipeline.py` banking predicate moved from "overall_status pass" to "escalation not page". (This
   also expresses the pre-existing intraday-is-informational intent through the same gate: the
   intraday cap already lowers a provisional PAGE to NOTICE, so an intraday fire banks.)

The `QcResult` stored contract/schema is unchanged; a downgraded constituent result is simply a
`qc_status=warn` + `severity=warning` row, the same shape any WARNING already had.

## Consequences

- **The index remains strict.** SX5E's CRITICAL gates are byte-for-byte unchanged. In particular,
  **SX5E's own genuine calendar inversion on 2026-06-18 is NOT masked by this change**: it is a real
  surface defect on the tradeable index, `is_index=True`, and correctly STILL pages CRITICAL and
  blocks the date. That defect is a separate issue handled elsewhere; this ADR does not touch it and
  must not be read as silencing it.
- **Constituent surface noise stops paging.** A constituent CRITICAL-gate failure becomes a NOTICE:
  it is recorded (a downgraded WARNING row in `qc_results` + triage, fully auditable), it shows in
  the report, but it does not page and does not block the date from banking healthy. EOD QC stops
  paging every night over expected single-name illiquidity.
- **A constituent-only-failing date banks.** When only constituents fail and the index is clean, the
  QC stage records OUTCOME_OK and the date banks (`last_healthy_trade_date` advances). When the index
  fails CRITICAL, the QC stage records OUTCOME_FAILED, `qc` stays in the backlog, and the date does
  not bank — exactly as before.
- **Behavioural note (deliberate):** the banking predicate moved from "overall_status == pass" to
  "escalation != page". A pure-WARNING report (no critical fail) now banks where before it did not.
  That is the correct reading of "healthy" (a warning is a note, not an incident) and is consistent
  with how the alert/escalation seam already treats WARNINGs; the existing stand-in banking tests
  (which tie `overall_status` to `escalation == page`) are unaffected.
- **No config-hash move.** This is engine logic (severity by role), not a threshold change; the
  `qc` config bundle and `qc.yaml` are untouched.
- **Frontend:** out of scope and untouched (owner-owned).

## Out of scope

Per-constituent severity tuning (some names liquid enough to warrant stricter gates) and any
constituent-level alerting/digest are a later concern. This ADR draws the one line the owner ruled:
index strict, constituents notice-only.
