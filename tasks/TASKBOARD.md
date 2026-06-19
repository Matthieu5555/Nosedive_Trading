# TASKBOARD

Collision guard for a shared `devs`-group workspace where several humans and agents work
at once. **Before you change files, claim them in the claim table below; clear your claim
when done.** It is advisory, not enforced — it only works if everyone reads and writes it.
The real safety is branch discipline: one branch per task, merge small and often, so
collisions surface as merge conflicts, never silent overwrites.

What was *built* lives in the code, the per-directory `README.md`s, and `TARGET.md`; the *why*
is in git history — not here. This board is only "who is touching what right now" and "what is
open to pick up." Finished specs move to [`tasks/archive/`](archive/); the changelog is git history.

> **▶ The destination is [`TARGET.md`](../TARGET.md)** — the single roadmap: the end-state
> capability map and the ordered build sequence (§7 is the pre-ordered gap list).

## Scope guardrail

Scope + universe model live in [`TARGET.md §0`](../TARGET.md) — **index-options-only, IBKR sole broker, SX5E sole live index**.
If any older spec, ADR, or README still says "Saxo", "Deribit", "three brokers", or "equity
underlying", **the index-only pivot wins** — do not resurrect it. The gate (the only one) is in
`AGENTS.md`; **green 2026-06-17** after the post-fleet cleanup pass (`uv run pytest -q`: 2998 passed, 12 skipped; frontend `npm test`: 376 passed).

> **⛔ Standing guardrails.**
> 1. **Ground in the reference:** every design/scope decision traces to `docs/blueprint/` + the
>    course transcripts `docs/transcripts/`. Read them first; don't ask the owner what they specify.
> 2. **Full-membership constituent option capture is ON; Matthieu's multi-page front is canonical.**
>    The owner reversed the old prices-only move (former ADR 0051) on 2026-06-19 — constituent option
>    chains are captured again (full membership) and the multi-tab cockpit is the right direction. Do
>    not re-introduce prices-only capture, the `top_n` gate, realized-vol-only ρ̄, or page-reduction.
> 3. **The frontend is owner-owned.** `apps/frontend/web/**` is Matthieu's lane — do not edit it.
>    Backend/BFF analytics may add *additive read-only* serializer fields, never web/React files.

## Active claims

| Who | Area / files | Claimed | Note |
|-----|--------------|---------|------|
| Matthieu (frontend) | `apps/frontend/web/**` | 2026-06-17 | Owner-owned frontend lane (cockpit-ux / design-language). Off-limits to the fleet — see archived [frontend-cockpit-ux-overhaul](archive/frontend-cockpit-ux-overhaul.md) for prior context. |
| Claude (surfaces) | `surfaces/projection.py`, `surfaces/market_state.py`, `actor/driver.py` (projection wiring), projection tests | 2026-06-19 | Defect 5 (forward=spot ignores carry) + Defect 4 (per-side ATM parity violation): carry-implied forward + one-vol-per-strike + parity QC. |

## Ready queue — unclaimed, pick one and claim a row above

The board was cleared to **MAT-LEGIBILITY only** on 2026-06-17 — a fleet of family leads
(one lead + tester + implementer-per-task) built or assessed every other open lane and the
specs were archived (see git history / [`archive/`](archive/)). The sole remaining active
cluster:

**MAT-LEGIBILITY cluster (owner ask 2026-06-17, "the user must know wtf is going on")** — surface,
on Onglet 1, how much of the captured chain the strict surface rests on, why rows were excluded,
and let the PM pick strict vs indicative without confusing them. Three disjoint specs, land in
order; share one coverage/two-sided-fraction contract. All read-only / additive except #3. **These
touch the frontend and are therefore the owner's to sequence** — listed here so they are not lost.
- [MAT-LEGIBILITY-coverage-headline](MAT-LEGIBILITY-coverage-headline.md) — first, cheapest. "Nappe sur X / Y cotations · Z exclues" headline under the 3D nappe off `qc_results`; additive `coverage` block on `/api/analytics`.
- [MAT-LEGIBILITY-quarantine-drilldown](MAT-LEGIBILITY-quarantine-drilldown.md) — disclosure off the headline: *why* rows were excluded, re-derived read-only from `raw_market_events` via the shared capture predicate. Reconciles with the headline.
- [MAT-LEGIBILITY-strict-indicative-mode](MAT-LEGIBILITY-strict-indicative-mode.md) — biggest, last; net-new engine compute. Strict⟷Indicative toggle with an unmissable "INDICATIF" badge. **⛔ strict stays canonical/stored; indicative is a non-persisted view-time overlay that never reaches the risk/strategy path.** One open owner/quant decision first: what indicative actually prices.

## Owner-action residuals — archived as code-complete, the last inch is yours

These specs are **off the board** (built and archived) but carry a deliberate owner decision the
fleet would not make for you. None block paper operation.
- **Live order transmission** ([execution-order-sign-and-send](archive/execution-order-sign-and-send.md)) — paper path landed, `EXECUTION_TRANSMIT_ENABLED` absent ⇒ live transmit OFF. Flipping it on needs the owner `live` flag + a ticket-bound sign-off + a recorded-green `EXECUTION_SECURITY_REVIEW`.
- **IBKR unattended re-auth** ([ibkr-unattended-reauth](archive/ibkr-unattended-reauth.md)) — alert + runbook landed; the no-SMS OAuth path is blocked on the IBKR Self-Service "Enable OAuth Access → 400" enrollment (owner portal action).
- **2026-06-16 data promote** ([T-clean-ingestion-2026-06-16](archive/T-clean-ingestion-2026-06-16.md)) — recompute validated in a temp store (canonical `data/` untouched). Go/no-go on swapping the recomputed day into non-git-recoverable canonical `data/`, plus 06-15 keep/drop, is the owner's.
- **FuturesPoint capture** ([1D-futures-term-structure](archive/1D-futures-term-structure.md)) — blueprint amendment landed; ADR 0053 is Proposed pending an owner ruling on the `FuturesPoint` contract shape + roll convention before capture code lands.
- **Carry-split default** — stays parity-implied (byte-identical); the flat-rate capability is built + tested but not the yaml default. Owner flips if/when wanted.

## Format

`| your-name-or-agent | infra/foo/... | 2026-06-13 | short intent |`
