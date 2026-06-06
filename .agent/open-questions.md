# Open questions — the pending-decision register

This is a **reference** doc: a living register of decisions that matter but have **not
been ruled on yet**. It is the counterpart to `.agent/decisions/` — that directory holds
choices already *made* (append-only ADRs); this file holds choices still *open*.

For: any agent or human who hits a fork that is not theirs to settle (an owner's call, a
domain ruling the blueprint must give, a dependency on an external answer). Instead of
guessing — or silently picking one and burying it in code — record it here so the next
person sees it is open and who owns it.

## How to use it

- **Add a row** the moment you hit an important choice you cannot resolve from the
  request, the code, or the blueprint. Give it the next `OQ-N` id. Keep the one-line
  summary in the table; if it needs more than a line, add a `### OQ-N` block below.
- **Do not pre-decide.** A row here means "blocked on a ruling," not "here is my plan."
  State the options neutrally and who decides.
- **Close it** when it is ruled: move the row to *Resolved*, record the outcome, and link
  the ADR if the decision was big enough to warrant one. A resolved open-question that
  produced an ADR is the normal lifecycle: `OQ-N (open) → ruling → ADR 00NN (accepted)`.

The register is indexed from `AGENTS.md` (the "Decisions" section) so it is discoverable
without searching.

## Open

_None currently. OQ-7 was ruled on 2026-06-06 (see Resolved); OQ-1 through OQ-6 were ruled
on 2026-06-05._

_OQ-1 through OQ-4 were ruled on 2026-06-05; see Resolved. The `(blueprint)` rulings (OQ-1, OQ-3)
and futures capture still need a blueprint amendment + ADR to land formally — those follow-ups are
tracked in [`documentation/roadmap-index-analytics.md`](../documentation/roadmap-index-analytics.md)
§6, not as open questions._

### OQ-1 — "dollars per what" for monetized Greeks

`infra/risk` already computes monetized sensitivities beside the raw Greeks, so both
representations exist. The open part is the **convention each dollar figure is quoted in**,
which must be fixed and carried (with units) into the front contract. Concretely: is the
dollar Greek **per contract or per position**, and per what move — e.g. **delta $** per
\$1 of underlying, **vega $** per **1 vol point**, **theta $** per **calendar day**,
**gamma $** per **1% move**? Until this is set, the BFF should carry the raw value *and*
an explicit unit string per metric rather than a bare number. Surfaced first in the
medium-term vision (see `documentation/vision-medium-term.md`).

### OQ-2 — deep historical options data source

The compute skeleton (IV → surface → projection → Greeks) is built and pure; the bottleneck
is **feeding it history**. The TWS/REST IBKR connection (ADR 0008 / [ADR 0024](decisions/0024-ibkr-rest-transport-alongside-tws.md))
covers live and recent data well, but "the max of daily historical snapshots" across index +
constituents may need a different vendor for real depth. This is an *alimentation* decision,
not a design one — it does not block building the pipeline, but it blocks the pipeline being
useful, so decide it early.

## Resolved

| # | Question | Ruling | Recorded in |
|---|----------|--------|-------------|
| OQ-7 | Blueprint data-dictionary field names vs. code contract field names (`forward_price`/`forward`, `implied_vol`/`iv`, `log_moneyness`/`k`, `scenario_pnl`/`pnl`, `qc_status`/`status`, `dollar_*`/`cash_*`). | **Follow the blueprint — code conforms.** Owner ruled "suivre le blueprint, repartir de zéro pour la data" (2026-06-06): rename the frozen contract fields to the Part IX names; data starts fresh so the Parquet/hash schema change needs no migration. `InstrumentKey.broker_contract_id` left as-is (embedded in the canonical key, not a standalone column) — flagged as an optional follow-up. | [ADR 0029](decisions/0029-contract-field-names-conform-to-blueprint.md) |
| OQ-0 | Does IBKR-over-REST belong in the build, given the Nautilus-spine direction (ADR 0023)? | Yes — build the custom IBKR-REST connector into the catalog (Saxo/Deribit pattern), REST preferred, Nautilus-TWS as a manual-flip fallback. Owner ruled 2026-06-05. | [ADR 0024](decisions/0024-ibkr-rest-transport-alongside-tws.md) |
| OQ-1 | Greek/metric monetization convention — "dollars per what"? | Store raw per-unit Greeks as source of truth; expose a dollar layer with explicit units — **Delta\$ = Δ·S·mult** (per \$1), **Gamma\$ = Γ·S²/100** (per 1% move), **Vega\$** per 1 vol point (0.01), **Theta\$** per calendar day (÷365), **Rho\$** per 1% rate; per-contract→per-position additive. Gamma normalisation (1% vs \$1) and theta day-count (365 vs 252) become config flags. Owner ruled 2026-06-05; blueprint amendment + ADR pending. | [roadmap](../documentation/roadmap-index-analytics.md) §2 |
| OQ-2 | Historical data source | **IBKR is the source** (owner/prof mandate; Yahoo excluded as unreliable). Underlying daily price history (index + every constituent, for charts) is feasible via IBKR historical bars (`reqHistoricalData` / `/hmds/history`) — years of daily depth, ~51 requests, within pacing — but **not yet implemented** in our adapter (`cp_rest_adapter.py` is live snapshot + WS only). Deep option-chain history is IBKR's weak spot, so the options dataset is built **forward** via daily close snapshots (IBKR best-effort backfill at the start). No third-party vendor by default; revisit only if a deep options backfill proves necessary and IBKR insufficient (prof's call). Owner ruled 2026-06-05; building the IBKR historical-bar fetch is a Phase-0 task. | [roadmap](../documentation/roadmap-index-analytics.md) §2 |
| OQ-3 | Index membership: point-in-time vs current | Point-in-time is mandatory — store each constituent with `(effective_add_date, effective_remove_date)` and as-of weights; never apply today's list to past dates. Source Siblis Research (SX5E + SP500), cross-checked vs STOXX / EODHD / CRSP. Gate joins with `check-lookahead-bias`. Owner ruled 2026-06-05; blueprint amendment + ADR pending. | [roadmap](../documentation/roadmap-index-analytics.md) §2 |
| OQ-4 | Tenor grid — confirm the exact set | **10d, 1m, 3m, 6m, 12m, 18m, 2y, 3y** (the prof's spoken grid; resolves the `12m`/`1an` duplicate and out-of-order tail). Owner ruled 2026-06-05; pin into the blueprint data dictionary, confirm against the course's formal brief. | [roadmap](../documentation/roadmap-index-analytics.md) §2 |
| OQ-5 | `StorageRepository` port: make it load-bearing or delete? | **Keep + make load-bearing.** Owner ruled storage follows Vincent's blueprint-aligned architecture: raw in **`.parquet`** (prof mandate, no doubt), **DuckDB** addable later as a query engine over parquet, **SQLite** for the higher layers later — a real multi-backend future, which is exactly what the port (already accepted in [ADR 0015](decisions/0015-storage-repository-port-tiered-backends.md)) exists for. So: type infra/orchestration/host signatures against `StorageRepository`; widen the port only where a caller has a legitimate uncovered need; never delete it. The duplicate non-Vincent storage/event leftovers collapse via the C6 collection-seam unification ([ADR 0027](decisions/0027-collection-seam-push-canonical.md)), not here. Owner ruled 2026-06-05. | this register + [ADR 0015](decisions/0015-storage-repository-port-tiered-backends.md) |
| OQ-6 | On-disk profile format / reproducibility anchor | **Profiles are effective-dated + content-addressed in a runtime config store; a run freezes the resolved config in its manifest — git is dev-time only.** Reproducibility includes replaying a **past day**, so config carries the platform's as-of discipline (like market data + index membership): per-run **manifest freeze** (replay a run) + **as-of resolution** of effective-dated profiles (reconstruct a past day). A name → append-only versions; a run pins an immutable hash. Stage: YAML overlays + manifest freeze now → SQLite metadata store → API CRUD; same model throughout. Owner ruled 2026-06-05. Corrects the draft (git is *not* the run-time record; the per-run snapshot is essential, plus the temporal dimension). | [ADR 0028](decisions/0028-configuration-and-reproducibility-standard.md) |
