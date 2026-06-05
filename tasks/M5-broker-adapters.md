# M5 — Broker adapters (IBKR, Saxo, Deribit)

- **Branch:** `feat/merge-brokers` (or one sub-branch per broker; they are mutually orthogonal)
- **Owns:** `packages/infra-ibkr/**`, `packages/infra-saxo/**`, `packages/infra-deribit/**` (each its own package: transports, discovery, collectors, configs, samples, tests).
- **Depends on:** M0 (`BrokerSession` protocol), M4 (the frozen adapter-to-actor wiring + chain-selection policy).
- **Blocks:** nothing downstream; this is where Vincent's biggest lead lands.

## Objective

Bring Vincent's three broker integrations into the merged repo as `infra-*` packages, each satisfying M0's `BrokerSession` protocol and plugged into M4's actor via the wiring M4 froze. The three brokers are independent of each other — this workstream parallelizes internally three ways.

## What to merge, per broker

- **IBKR.** Bake-off: our live `IbkrBrokerSession` over `ib_async` (`backend/src/connectivity/ibkr_session.py`, optional `ibkr` extra, read-only `StartupFetchNONE`, IB tick-type → `BrokerTick` mapping, conId-keyed resolved rows, spot-windowed chain with median fallback) vs Vincent's `infra-ibkr` (`collectors/{ibkr_adapter,ibkr_discovery}.py`, `connectivity/ibkr_transport.py`, `flow.py`) **plus** the choice of Nautilus's built-in IBKR adapter. Keep whichever gives the cleaner `BrokerSession` impl; carry Vincent's **real captured samples** (`samples/{asml_real_2026-06-05,spy_real_2026-06-04}.json`) and his `test_real_sample_reconstruct.py` regardless.
- **Saxo** (Vincent-only — adopt wholesale, adapt to the seam). `infra-saxo`: the full **OAuth2 flow** (`auth/{web_oauth,token_manager,token_persist,env_tokens}.py`), `collectors/{saxo_adapter,saxo_discovery,saxo_underlying}.py`, `connectivity/saxo_transport.py`, `config.py`, `flow.py`, configs, the real sample (`samples/asml_real_2026-06-04.json`), and its substantial test suite. Wrap it behind `BrokerSession`; keep secrets out of git (tokens live in `$HOME`/`.env`, per `AGENTS.md`).
- **Deribit / crypto** (Vincent-only — adopt wholesale). `infra-deribit`: `collectors/{deribit_adapter,deribit_discovery}.py`, `connectivity/deribit_transport.py`, `flow.py`, configs, tests. Crypto conventions (funding, perpetuals) differ from equity options — keep his glossary/notes; M9 folds them into the merged glossary.

## Frozen seam

Each broker package exposes exactly one `BrokerSession` implementation and nothing broker-specific above it. M4's actor must drive all three identically; the only difference is transport + entitlement, never analytics.

## Test surface

Read [TESTING.md] first. Specific to M5:
- Each broker: a fake transport drives the full adapter → `BrokerSession` → M4 plane, no live socket in the suite (the live-broker ban stands; live sockets prove out via scripts, not pytest).
- Real-sample reconstruct per broker (carry Vincent's `test_real_sample_reconstruct.py`): a captured real chain replays into the same normalized raw events deterministically.
- Saxo OAuth: token refresh/persist/expiry paths tested against a fake auth server; no real token in the repo.
- The same actor produces structurally identical outputs from any of the three brokers (the broker-agnostic guarantee, checked).

## Done criteria

Three `infra-*` packages, each a `BrokerSession` over its transport, all driving M4's actor identically, real samples + reconstruct tests carried over, OAuth tested with no secrets in git, gate green. IBKR bake-off resolved to one implementation.

## Gotchas

No secrets in git — Saxo tokens are the trap; they live in `$HOME`/`.env`, gitignored. Don't let a broker's quirks leak above `BrokerSession` — entitlement/status handling belongs in `market_data_policy` (M4), not in the analytics. Keep the three packages independent: `infra-saxo` must not import `infra-ibkr`. Live sockets are never in the test suite.
