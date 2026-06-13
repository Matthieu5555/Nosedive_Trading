# 0042 — Index-options-only scope: IBKR is the sole live broker, SX5E first, SPX parked

- **Status:** accepted by workspace-owner direction, 2026-06-13. Records the scope ruling that
  `tasks/T-index-only-refactor.md` implemented (landed same day). **Reverses decision 3 of
  [[0023-nautilus-runtime-spine-and-library-leverage]]** ("keep Vincent's Saxo/Deribit"); the rest
  of 0023 (Nautilus is the runtime spine, lean on libraries, retire the `ib_async` session) stands.
- **Date:** 2026-06-13.
- **Implements:** the owner scope call (memory `index-only-app-scope`); stays inside
  [[0011-blueprint-as-plan-of-record]] (the blueprint governs the *option-analytics* domain, which
  is unchanged — this narrows the *instrument and broker* surface, not the math).
- **Relates to / amends:** [[0013-infra-deribit-crypto-adapter]] + [[0014-infra-saxo-adapter]]
  (superseded — packages removed), and carries a dated `AMENDED 2026-06-13` note on every ADR that
  still spoke multi-broker: [[0012-per-broker-leaf-packages]], [[0017-provider-dimension]],
  [[0022-m5-vendored-broker-slice]], [[0023-nautilus-runtime-spine-and-library-leverage]],
  [[0025-nautilus-host-catalog-topology]], [[0026-orchestration-observability-reconciliation]],
  [[0027-collection-seam-push-canonical]], [[0035-index-registry-and-per-index-capture-schedule]].

## Context

The repo reached its current shape by **merging two independent builds toward the max-union of
both** (this workspace + Vincent's `AlgoTrading`; see the TASKBOARD "merge/convergence" section).
That union pulled in Vincent's framework wholesale — including a **multi-broker ambit** the actual
project never needed: a crypto leaf (Deribit, ADR 0013), an equity-broker leaf (Saxo, ADR 0014),
the per-broker leaf-package rule (ADR 0012), the provider dimension motivated by "Saxo *and* IBKR
capture the same symbol" (ADR 0017), and the "keep the vendored Saxo/Deribit slice" resolution
(ADRs 0022/0023). Seven of the 41 ADRs are direct transcriptions of Vincent's framework ADRs; ~half
the corpus references the merge.

The project's real target (TARGET.md, the roadmap) is **index options analytics** — trade and
analyse index option chains and their constituents, with **IBKR as the data/execution broker the
owner mandated** (OQ-2: "IBKR is the source"). Saxo and Deribit were never on the critical path;
they were dead weight carried in from the union: 91 files, two OAuth flows, broker seams, and tests,
plus a multi-broker narrative across a dozen ADRs that a fresh agent had to read and discount. That
narrative is the **context-pollution cost of a max-union merge**: decisions about machinery the
product does not ship, presented at the same "accepted" weight as load-bearing ones.

## Decision

**The application is index-options-only, with a single live broker.** Concretely:

1. **IBKR is the sole live broker.** The `infra-saxo` and `infra-deribit` leaf packages were removed
   entirely (leaf adapters with zero core-import coupling; git history is the archive). `infra-ibkr`
   is the only surviving leaf. The `provider` partition dimension (ADR 0017) **stays** — it is
   generic infra and load-bearing for IBKR-only capture, and lets another broker rejoin later under
   the same key — but no second broker exists today.
2. **SX5E (EuroStoxx 50) is the single live index; SPX is parked.** `SPX.enabled: false` in
   `universe.yaml` — kept in the registry as the cheapest multi-index proof, re-enabled by flipping
   one flag. The live EOD spine exercises SX5E (calendar XEUR) end to end.
3. **Single names are index *constituents*, never option underlyings.** `UniverseConfig.underlyings`
   was removed; the index registry is the single universe source. `data/reference/index_constituents/`
   is kept (constituents back the membership/weights and the per-component candlestick charts).
4. **The decision lives here, in the ADR ledger** — not only in a task file and memory — because it
   is the most consequential scope call of the week and reverses a standing ADR. Task files get
   archived; this record does not.

## Consequences

- **A fresh agent orients to the narrow scope from the decisions/ dir**, not by reverse-engineering
  it from a task file. Every multi-broker ADR now carries a dated amendment pointing here; reading
  the amendment is enough to skip the stale body.
- **The gate stays green without Saxo/Deribit** (ruff/mypy/lint-imports/pytest; the broker layer in
  import-linter names `infra_ibkr` only). The only failing tests are the pre-existing
  `documentation/`-deletion ones (owner: leave).
- **No analytics or math changed.** The blueprint-governed option-analytics domain (IV, surface,
  Greeks, forward, risk) is untouched; this is purely an instrument/broker-surface narrowing.
- **Re-entry is cheap and bounded.** Re-adding a broker = a new `infra-<broker>` leaf under the
  unchanged provider dimension; re-enabling SPX = one flag. Neither requires reversing this ADR,
  only a follow-up that records the re-expansion.
- **Lesson for future merges:** a max-union merge imports the other repo's *scope*, not just its
  code. Prefer a scope-first merge (build the minimal target, graft proven pieces) over union-then-
  prune. This ADR is the prune.
