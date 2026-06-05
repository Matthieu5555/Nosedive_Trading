# config

Every number that affects economics, in one validated, versioned, content-hashed
object. The config object is also one half of the determinism guarantee: its hash
is stamped onto every derived record, so a result can always be traced back to the
exact settings that produced it.

## Why this exists

Economic constants — the staleness threshold, the IV solver's tolerance, the
stress grid — must not be scattered as literals across modules. If they were, two
modules could disagree, a result could not be traced to the settings that made it,
and "reproduce yesterday's surface" would be impossible because nobody could say
what yesterday's settings were. So they live here, in one frozen `PlatformConfig`,
split into four independently versioned sections, with a stable content hash that
lets a historical computation be reproduced and checked.

A hard boundary runs through this module: economics is in, environment is out.
Where data lives on disk, which host runs the service, which port to bind — those
are environment, not economics, and they are deliberately kept out of
`PlatformConfig` so they never move the reproducibility hash. The storage root,
for instance, is passed to the app separately (the orchestration layer owns it),
not read from here.

## The public interface

Import from `config`:

- `PlatformConfig` and its four section types: `UniverseConfig`,
  `QcThresholdConfig`, `SolverConfig`, `ScenarioConfig`.
- `load_config(path)` — read a TOML file into a validated `PlatformConfig`.
- `config_from_mapping(data)` — build one from an already-parsed mapping (e.g. a
  dict from `tomllib`), for callers that loaded the TOML themselves.
- `config_hash(config)` — the SHA-256 of the whole config. Moves when any
  economic field in any section moves. This is the value stamped onto derived
  records.
- `section_hash(config, section)` — the hash of one named section. Moves only
  when that section's fields move.
- `section_versions(config)` — the four independent version strings, keyed by
  section name.
- `canonical_json(value)` — the canonical JSON form of any config object or
  section (sorted keys, fixed separators), the input both hashes are built from.
- `SECTION_NAMES` — the four section names in hash order.
- `ConfigError` — raised when a file or mapping is missing a required section or
  field.

## The four sections and every field

The config is one frozen `PlatformConfig` holding four sections, each with its own
`version` string. The four versions are independent on purpose: bumping the solver
version says "the solver changed" without pretending the scenario grid changed
too. Bump a section's `version` whenever you change how that part behaves; the
change then shows up in that section's hash and in the overall `config_hash`.

`universe` (`UniverseConfig`) — which instruments the platform tracks.

| Field | Type | Meaning |
|-------|------|---------|
| `version` | `str` | This section's version stamp. |
| `underlyings` | `tuple[str, ...]` | The tracked underlying symbols (e.g. `AAPL`, `MSFT`, `SPY`). A TOML list becomes a tuple. |
| `exchange` | `str` | The exchange routing label (e.g. `SMART`). |

`qc_threshold` (`QcThresholdConfig`) — the cut-offs that decide whether a quote or
chain is usable.

| Field | Type | Meaning |
|-------|------|---------|
| `version` | `str` | This section's version stamp. |
| `max_spread_pct` | `float` | Reject a quote wider than this fraction of mid (default `0.05` = 5%). |
| `max_quote_age_seconds` | `float` | An option quote older than this is stale (default `30.0`). |
| `min_chain_count` | `int` | Minimum eligible calls+puts per maturity (default `6`). |

`solver` (`SolverConfig`) — how the implied-volatility inversion is run.

| Field | Type | Meaning |
|-------|------|---------|
| `version` | `str` | This section's version stamp. |
| `iv_tolerance` | `float` | IV inversion convergence tolerance (default `1e-8`). |
| `max_iterations` | `int` | Maximum solver iterations (default `100`). |

`scenario` (`ScenarioConfig`) — the stress grid the risk engine applies.

| Field | Type | Meaning |
|-------|------|---------|
| `version` | `str` | This section's version stamp. |
| `spot_shocks` | `tuple[float, ...]` | Fractional spot moves, e.g. `(-0.10, -0.05, 0.0, 0.05, 0.10)`. |
| `vol_shocks` | `tuple[float, ...]` | Absolute vol moves, e.g. `(-0.05, 0.0, 0.05)`. |

The defaults above are the shipped `configs/default.toml` at the repository root
(not under `backend/`). That file is the canonical example of the on-disk shape.

## Data flow

```text
configs/default.toml
        |
   tomllib.load
        |
        v
  config_from_mapping  --(missing section)-->  ConfigError
        |
        v
   PlatformConfig  ----------------------------> config_hash / section_hash
        |                                               |
        | (passed to drivers/jobs)                      v
        v                                      stamped onto every
   analytics / risk / actor                    derived record's provenance
```

`load_config` is the only place that knows the on-disk shape. It reads the TOML,
then `config_from_mapping` turns lists into the tuples the frozen dataclasses
expect and coerces the numeric fields. The resulting `PlatformConfig` is passed
into the analytics, risk, and actor code as a parameter (dependency injection,
not a global), and its `config_hash` is what each derived record's provenance
stamp records.

Both hashes are deliberately built from canonical JSON — sorted keys, fixed
number formatting — hashed with SHA-256, never from Python's built-in `hash()`.
`hash()` is salted per process, so a dict hashed today and tomorrow differ;
SHA-256 of canonical JSON is identical on every machine, in every run, forever.
That stability is the entire reason the hash can anchor reproducibility.

## Failure modes

`load_config` and `config_from_mapping` raise `ConfigError` when a required
section is missing, naming the section (e.g. `config is missing required section
'solver'`) rather than surfacing a raw `KeyError` from deep inside. A field that
is present but the wrong type still surfaces as the underlying coercion error
(e.g. `float("x")`), not a `ConfigError` — the loader validates section presence
and coerces, it does not range-check economics. `section_hash` raises `KeyError`
for an unknown section name rather than silently hashing nothing, so a typo fails
loudly. None of these are retryable: a bad config is a fix-the-file problem.

## Fastest way to exercise it

```python
from pathlib import Path
from config import load_config, config_hash, section_versions

config = load_config(Path("configs/default.toml"))
print(config.qc_threshold.max_quote_age_seconds)  # 30.0
print(config_hash(config))                          # stable SHA-256 hex
print(section_versions(config))                     # {'universe': '2026.05.31', ...}
```

From `backend/`, the config behavior is pinned by `tests/test_config.py`; run it
with `uv run pytest -q tests/test_config.py`.
