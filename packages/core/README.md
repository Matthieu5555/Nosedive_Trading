# core — the shared foundation (level 0)

`algotrading.core`: cross-cutting, domain-agnostic primitives that every layer (infra,
strategy, execution, frontend) may import and that depend on **nothing above them**.
import-linter enforces that this is the bottom of the stack.

## What lives here

- **`config/`** — config loading + hashing. `yaml_config.py` (`load_yaml_config` →
  `LoadedConfig`), `loader.py` (`from_config` / `config_from_mapping` →
  `PlatformConfig`; `ConfigError`), `platform_config.py` (the typed `PlatformConfig` and
  its domain sub-configs: `QcThresholdConfig` (with its nested `GridQcConfig` grid-QC
  cut-offs, WS 1H), `ScenarioConfig`, `SolverConfig`, `UniverseConfig`, …), and the hashes
  (`config_hash`, `object_config_hash`, `composite_config_hash` — digests via
  `core.hashing`). The
  config standard this implements is
  [ADR 0028](../../.agent/decisions/0028-configuration-and-reproducibility-standard.md); the
  application work ([C7](../../tasks/archive/C7-config-hardening.md)) landed in full.
  `UniverseConfig.indices` carries the raw, *unvalidated* index-registry block (ADR 0035)
  so it folds into `config_hashes["universe"]` with no separate hash; the typed parse +
  calendar-code validation deliberately lives one layer up (`algotrading.infra.universe`,
  which owns the `exchange_calendars` dependency core stays blind to). The loader
  special-cases that one nested-map field via `build_dataclass`'s `caller_supplied` escape
  hatch — every flat economic field still goes through the no-silent-default reflective seam.
- **`hashing.py`** — the canonical-JSON + SHA-256 primitives every content hash is built
  from (M25): `canonical_dumps` (the *bare* convention — sorted keys, compact separators,
  values verbatim) and `sha256_hex`. The repo deliberately keeps three named canonical-JSON
  conventions because they feed persisted hashes (see the module docstring); the encoding
  and digest now have one reviewed home, gated by golden-hash pins in the test suites.
- **`provenance.py`** — the `ProvenanceStamp` every derived record carries (which inputs,
  which code version, which config hash) and the stamp helpers, including `snapshot_stamp`
  for the common one-snapshot emission shape (every source row shares one observation
  timestamp). This is the mechanism behind the platform's determinism and reproducibility
  guarantees.
- **`manifest.py`** — the run manifest (the per-run record that makes a run reproducible).
- **`log.py`** — structured (JSON) logging on stdlib `logging`: `get_logger(name)` returns
  a logger whose handler renders each record as one-line JSON via a custom `JsonFormatter`
  (it lifts any non-reserved `extra=` keys into the payload). No `structlog` dependency in
  core itself — but it is cooperative: when a process entrypoint has run
  `algotrading.infra.observability.configure_logging()` (the platform-wide structlog
  configuration, which marks its root handler with `log.HANDLER_MARKER`), `get_logger`
  attaches no per-logger handler and lets records propagate into that one root JSON
  stream, so a process never emits two formats. Standalone behavior is unchanged when
  nothing configured the root.

## Why it's separate

Keeping config/provenance/manifest/log in a dependency-free level-0 package is what lets
the analytics core stay pure and framework-free: a pure function takes typed config and
returns stamped data without reaching for a logger singleton, a global clock, or a broker
SDK. Everything above imports `algotrading.core`; `algotrading.core` imports nothing of
ours.

## Verify

```
uv run ruff check packages/core/src
uv run mypy .
uv run pytest packages/core/tests -q
```
