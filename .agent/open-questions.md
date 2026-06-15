# Open questions — the pending-decision register

Decisions that matter but are **not yet ruled on** — the counterpart to
`.agent/decisions/` (choices already made). Hit a fork that is not yours to settle
(an owner's call, a domain ruling, an external dependency)? Add the next `OQ-N`
row instead of guessing or burying a silent pick in code. When it's ruled, delete
the row; if it warranted an ADR, that ADR is now the record.

## Open

| # | Question | Status |
|---|----------|--------|
| OQ-10 | **`underlying` is keyed by a bare ticker, not market-qualified — cross-index symbol collisions.** SPX and SX5E share tickers naming *different* companies (`DG`, `DTE`, `EL`); an `underlying`-keyed table can hold only one of each pair. Needs an owner ruling on market-qualified keying (`SYM.MIC` or a per-market symbol column) — touches partitioning, the membership join, and the front's symbol display. | **Dormant.** SPX is parked (`enabled:false`); SX5E is the sole live index, so nothing collides today. Wanted before SPX re-enables; blocks nothing live. |
| OQ-11 | **`ScenarioResult` persists `rate_shock` but not `correlation_shock` — the correlation axis is not distinguishable on replay.** The 2026-06-14 audit's P1.1 fix added `rate_shock` to the stored stress row; the correlation axis (`infra-named-scenarios-and-corr-shock`) landed *after* that audit, so `Scenario.correlation_shock` exists on the config side but the persisted result row drops it — a stored ρ̄-bump cell reads identically to ρ̄+0.0. The clean fix mirrors P1.1 (additive-nullable `correlation_shock: float \| None = None` populated from `cell.scenario.correlation_shock`). Not applied unilaterally: outside the four audited P1s, and the correlation axis is dormant on the live book. | **Open — owner to rule.** Apply now (provenance symmetry with `rate_shock`) or defer until the correlation axis goes live? See `tasks/T-audit-2026-06-14-findings.md`. |
