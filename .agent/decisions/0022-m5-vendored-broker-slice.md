# 0022 — M5 broker adapters: vendored collector/universe slice (contests 0020)

- **Status:** accepted by workspace-owner direction — **contests [[0020-market-data-actor-wiring]]**.
  Recorded so the divergence is visible to the M4 owner, not silent.
- **Date:** 2026-06-05
- **Workstream:** M5 (broker adapters — IBKR / Saxo / Deribit)
- **Relates to:** [[0018-monorepo-keystone-m0]], [[0019-one-immutable-raw-model]],
  [[0020-market-data-actor-wiring]], [[0003]] (scalar `BrokerSession` seam).

## Context

M5 brings Vincent's three broker integrations into the monorepo as `packages/infra-{ibkr,saxo,
deribit}`. When this work started, M4 had not landed: `packages/infra/{universe,collectors,
connectivity}` were bare, so Vincent's leaves — which import `algotrading.infra.{collectors,
universe,connectivity}` and a richer market-data model — could not import at all.

While M5 was in flight, the M4 owner landed **ADR 0020 (accepted)**, which freezes the M5 seam as
the M0-thin `contracts.BrokerSession` + scalar `BrokerTick` over the M1 raw layer, folds
chain-selection into one broker-neutral `universe.chain_planning` policy, and states explicitly
that Vincent's collector modules are *"not vendored as a parallel module."* M4's modules,
however, still live in `backend/src` (flat) and are **not yet relocated** into `packages/infra`,
so a fully 0020-compliant leaf cannot import `universe.chain_planning` / `AvailableChain` /
`collectors.replay_day` today.

Faced with that gap, the **workspace owner directed** (twice, with the ADR-0020 conflict and the
M4-owner overlap spelled out and advised against) to **proceed by vendoring Vincent's slice
near-verbatim** rather than waiting on M4 or rewriting onto the thin seam now. This ADR records
that decision and exactly what diverges, so M4 can reconcile deliberately.

## Decision

1. **Vendor the minimal M4/M1 market-data slice into `packages/infra` (additive, new files).**
   - `collectors/{normalize,collector}.py` — the rich `BrokerTick`, `FeedFault`,
     `MarketDataAdapter` protocol, `RawCollector` (slim `__init__`, no replay/summary/config).
   - `universe/{contracts,discovery}.py` + a minimal `master.py` (only `UniverseError`) — the
     canonical instrument model and chain normaliser (slim `__init__`).
   - `connectivity/session.py` — the `BrokerTransport` protocol + session lifecycle.
   - `storage/{events,json_io}.py` — the collector-level `RawMarketEvent` (EAV, `Decimal`/`None`
     values, `provider`/`contract_id_broker`) and its committed-sample JSON codec.
   No existing M1 file is overwritten; the only edit to a landed file is additive exports in
   `storage/__init__.py`.

2. **Port the three broker leaves near-verbatim, minus `flow.py`.** Each leaf exposes its
   transport, native chain discovery, and a `MarketDataAdapter` (Saxo also: the full OAuth2
   `auth/` package + `config`). `flow.py` (the `ProviderFlow` orchestration) is **dropped** —
   it imports the analytics pipeline (`forwards/iv/qc/snapshots/surfaces/orchestration`,
   `collectors.strike_selection`) that is not yet in `packages/infra`. `ib_async` is an optional
   extra (absent from the gate per [[0018-monorepo-keystone-m0]]); `httpx`/`websockets` are real
   deps. Small mypy/ruff-compliance edits were applied (lazy-import hygiene, explicit narrowing,
   missing annotations) — behaviour unchanged; Vincent's test suites pass.

## The divergence from 0020 — the exact collision surface for M4

Three pairs now coexist, by design of this decision; M0's frozen `contracts` seam is left
untouched:

| Concept | 0020 / M0 (frozen) | Vendored here (M5) |
|---|---|---|
| Tick | `contracts.broker.BrokerTick` — scalar (`broker_contract_id,field_name,value,sequence,exchange_ts`) | `collectors.normalize.BrokerTick` — EAV (`instrument_key,field_name,value,underlying,provider,exchange_ts,contract_id_broker`) |
| Raw event | `contracts.tables.RawMarketEvent` — `value: float`, `canonical_ts`, `trade_date` ([[0019-one-immutable-raw-model]]) | `storage.events.RawMarketEvent` — `field_value: Decimal\|str\|None`, `provider`, no `canonical_ts/trade_date` |
| Session | `contracts.broker.BrokerSession` — *protocol* (`connect/.../ticks`) | `connectivity.session.BrokerSession` — connection *lifecycle class* |

Distinct module paths, so Python/mypy/import-linter are clean; the conflict is **conceptual and
governance-level**, not a build break.

## Deferred (to M4 / the analytics pipeline relocation)

- The leaves implement Vincent's `MarketDataAdapter`, **not** M0's `contracts.BrokerSession`;
  no `ReplayBrokerSession`, no `universe.AvailableChain` / `chain_planning` binding yet.
- `flow.py` and the full real-sample reconstruction (`reconstruct_day` → SVI surface). The
  carried samples are guarded at the **raw-event** layer instead (deterministic decode +
  round-trip + canonical-key round-trip), upgradable to a surface assertion when the pipeline
  lands.

## Reconciliation path

When M4 relocates its plane into `packages/infra`, this slice **collides on
`{collectors,universe,connectivity}` and the two `RawMarketEvent`/`BrokerTick` models** — a
visible merge conflict, exactly the recoverable signal `AGENTS.md` intends. M4 (as the seam
owner under 0020) decides the survivor: either retire this vendored slice and re-home the
broker adapters onto the thin `contracts.BrokerSession`, or promote the richer EAV model into
the frozen contract via M0. Until then, the M5 leaves are gate-green and usable for offline
sample replay and broker-agnostic capture.

## Test surface (landed, gate-green)

- Per broker: fake-transport / pure-frame tests drive discovery + adapter + (Saxo) OAuth token
  refresh/persist/expiry against a fake auth server — no live socket, no secret in git.
- Real samples carried (IBKR SPY+ASML, Saxo ASML) with a deterministic raw-event replay test.
- Cross-broker structural-identity test: Saxo+Deribit (IBKR when its extra is present) ticks
  normalize to one indistinguishable `RawMarketEvent` shape through the shared `RawCollector`.
- 134 passed, 4 skipped (IBKR live-wiring tests skip without `ib_async`); ruff + mypy +
  import-linter clean across the workspace.
