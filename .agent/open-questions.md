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
