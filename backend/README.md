# backend

Python service and quant logic for the workspace. Uses `uv`, targets Python 3.13.

## TL;DR

This holds the Workstream A foundation — the typed data platform every other
workstream builds on. What's here today:

- `src/contracts/` — the twelve immutable table contracts (the only objects that
  cross a workstream boundary), the composite instrument key, the table registry,
  and write-ahead validation.
- `src/config/` — the validated `PlatformConfig` (four independently versioned
  sections) and its cross-process-stable `config_hash`.
- `src/provenance/` — the stamp every derived record carries (source records,
  calc time, code version, config hash) with a deterministic content hash.
- `src/storage/` — DuckDB-over-Parquet read/write adapters keyed to the contracts,
  partitioned by layer / trade date / underlying. See `src/storage/README.md` for
  the append-only, partition, and schema-evolution rules.
- `src/fixtures/` — the shared "rogues' gallery": liquid chains, every named
  pathology, and a synthetic known-answer surface the analytics oracles use.

Not here yet: there is no FastAPI `app` object, and `main.py` is still the
`uv init` hello-world stub. Standing up the service is later-workstream work.

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
