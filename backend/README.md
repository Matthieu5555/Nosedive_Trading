# backend

Python service and quant logic for the workspace. Uses `uv`, targets Python 3.13.

## TL;DR

This is the strategy-agnostic data-and-pricing backbone: it turns raw Interactive
Brokers market data into provenance-stamped options analytics, and runs the same
code live and in replay. The pipeline is five workstreams (A–E), each a set of
packages under `src/` with its own `README.md` — read the package README for
detail; this file is the map.

```
   broker feed                                                        consumers
       │                                                            (backtest, ML,
       ▼                                                             research — later)
  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐                  ▲
  │ B  market│──▶│ B  raw   │──▶│ C        │──▶│ D risk │                  │
  │ data in  │   │ capture  │   │ analytics│   │ engine │                  │
  └──────────┘   └────┬─────┘   └────┬─────┘   └───┬────┘                  │
                      │ append-only  │ pure fns    │ on C's frozen pricer  │
                      ▼              ▼             ▼                        │
                 ┌─────────────────────────────────────────┐              │
                 │ E  actor: drives C/D, stamps outputs     │──────────────┘
                 │ E  orchestration + QC + historical replay│
                 └─────────────────────────────────────────┘
                                   │ reads/writes
                                   ▼
                 ┌─────────────────────────────────────────┐
                 │ A  contracts · config · provenance ·     │
                 │    DuckDB-over-Parquet storage · fixtures │
                 └─────────────────────────────────────────┘
```

*The diagram shows what feeds what. It omits the connectivity supervisor, metrics,
and alerting. Strategy/backtest/ML are deliberately out of scope — they re-enter
later as read-only consumers, never inside the plumbing.*

- **A — Foundation.** The typed substrate every other workstream speaks through.
  `src/contracts/` (the twelve immutable table contracts, the instrument key, the
  registry, write-ahead validation), `src/config/` (the validated `PlatformConfig`
  and its cross-process-stable `config_hash`), `src/provenance/` (the stamp every
  derived record carries, plus `validate_stamp`), `src/storage/` (DuckDB-over-Parquet
  adapters with the append-only / partition / schema-evolution rules), `src/fixtures/`
  (the shared rogues' gallery and known-answer oracles for tests).
- **B — Market-data plane.** Broker-agnostic connectivity that reconnects without
  silently dying, the instrument universe, and the append-only raw collector:
  `src/connectivity/`, `src/universe/`, `src/collectors/`.
- **C — Analytics core.** The pure-function quant heart — snapshots, the parity
  forward, the IV solver, the SVI surface, and the pricing engine:
  `src/snapshots/`, `src/forwards/`, `src/iv/`, `src/surfaces/`, `src/pricing/`.
- **D — Risk engine.** Portfolio greeks, monetized sensitivities, aggregation,
  broker reconciliation, and the scenario stress grid, all built on C's frozen
  pricer: `src/risk/`.
- **E — Integration & operations.** The framework-free actor that drives C/D and
  stamps their outputs, the QC/validation library, and orchestration +
  observability + historical replay: `src/actor/`, `src/qc/`, `src/orchestration/`
  (with the `reconstruction/` subpackage).

Not here yet: there is no FastAPI `app` object, and `main.py` is still the
`uv init` hello-world stub. Standing up the HTTP service is later work.

## Run

```
cd backend
uv sync
```

There is no app to serve yet, so the workspace README's `uvicorn main:app` command
does not work. Update this section when the `app` object lands.

## Verify

The full quality gate (wired in `pyproject.toml`) is:

```
uv run ruff check . && uv run mypy . && uv run pytest -q
```

It runs green on the current foundation. Tests live in `tests/`; `hypothesis` is
used for the invariants (provenance order-independence, etc.).

## Configure

Economics live in `/srv/project/configs/default.toml` — four versioned sections
(universe, qc_threshold, solver, scenario). Load it with `config.load_config`.
Environment settings (data root, hosts) are passed separately and deliberately do
*not* enter `config_hash`, so they never change a reproducibility hash.

## Conventions

Follows `/srv/project/.agent/conventions.md` — functional by default, type hints
everywhere, `pathlib`, structured logging, no `utils.py`. Quant/time-series code
must obey the look-ahead rules in that file. Keep this README current when you
change what the backend does.
