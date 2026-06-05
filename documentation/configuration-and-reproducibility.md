# Configuration & reproducibility — architecture and standard

This doc is **architecture + binding standard** (reference, with explanation). It defines how
every parameter in the platform is sourced, validated, and made reproducible, and it is the
rule the per-domain config tasks must conform to so they cannot diverge.

For: anyone writing or reviewing code that has a tunable number, threshold, window, bound,
cadence, or path — i.e. almost everyone. It assumes the `algotrading.core` config + provenance
modules as built.

**Authority.** This implements **blueprint Part VII (`documentation/blueprint/07-configuration.md`)**
and the versioning convention in **blueprint Part I**. The blueprint is the source of truth
(ADR 0011); where this doc and the blueprint disagree, the blueprint wins and this doc is the
bug. It does **not** restate broker-specific config — that lives in each leaf's README.

## The rule, and why

Blueprint Part VII, verbatim in intent: *"Configurations are economic inputs. They should never
live as scattered constants in notebooks or inside implementation files. Every threshold, bump
size, scenario grid, strike-selection rule, and cadence should be represented in a versioned
configuration artifact."*

So the standard is one sentence: **no business/compute parameter is a literal in a `.py` file.**
It comes from a validated config object, hydrated from a versioned YAML file, and the config's
hash is stamped onto every output it shaped — which is what lets a computation be reproduced and
checked byte-for-byte. The audit found this rule is written in `conventions.md` but not enforced:
the typed config exists yet no `infra` module loads it, so every domain fell back to literals.
This doc is the target state that closes that.

## Architecture — three layers

```text
   VERSIONED YAML (in repo, blueprint Part VII taxonomy)        ENVIRONMENT (NOT hashed)
   universe.yaml  qc.yaml  pricing.yaml  scenarios.yaml         environment.yaml
   broker.yaml          (+ optional profile overlay)            (paths, endpoints, host/port,
        |                                                         scheduler, log level)
        v   load_config(base, overlay) → deep-merge                      |
   LoadedConfig { data (frozen), config_hash, sources }                  v  injected as-is
        |                                                          runtime wiring
        v   parse + validate                                       (storage path, BFF host)
   TYPED per-domain config objects  ── never read by compute as raw YAML
   UniverseConfig · QcThresholdConfig · SolverConfig · SurfaceConfig · ScenarioConfig · …
   each carries:  version  +  from_config()  +  __post_init__ validation
        |
        v   passed as parameters (dependency injection)
   PURE COMPUTE   snapshots → forwards → iv → surfaces → pricing → risk
        |
        v   emits derived record + ProvenanceStamp{ config_hash, code_version, source_records }
   raw / derived store (.parquet)   ── config_hash makes every row reproducible
```

The diagram shows how a parameter travels from a YAML file to a stamped output. It omits the QC
and as-of machinery, which wrap compute but do not change this path. Read it top to bottom:
YAML files (plus an optional profile overlay) are deep-merged by `core.config` into a
`LoadedConfig` that already carries a `config_hash`; that data is parsed into **typed, validated**
per-domain objects; those objects are **passed as parameters** into the pure compute (never read
from disk deep inside a function); each derived record is emitted with a `ProvenanceStamp` whose
`config_hash` ties it back to the exact settings. Environment settings travel a separate path and
are **deliberately excluded from the hash** (see Reproducibility).

### Layer 1 — versioned YAML (blueprint Part VII taxonomy)

Use the blueprint's file set as the canonical base, not an invented scheme:

| File | Owns | In reproducibility hash? |
|------|------|--------------------------|
| `environment.yaml` | storage paths, service endpoints, log levels, scheduler settings | **No** — environment, not economics |
| `broker.yaml` | client-id bands, reconnect/backoff policy, session windows, market-data cadence | No (operational); *except* capture-selection cadence that changes what is recorded → judge |
| `universe.yaml` | monitored underlyings, exchanges, product families, maturity windows, strike-selection (delta band / `ChainSelection`) | **Yes** — selection shapes outputs |
| `qc.yaml` | quote filters, stale limits, solver thresholds, fit tolerances, anomaly z-bands | **Yes** |
| `scenarios.yaml` | named stress scenarios, shifts, combinations, report subsets, `roll_down_days` | **Yes** |
| `pricing.yaml` | IV solver bounds, finite-difference bumps, SVI bounds/tolerances, forward-confidence heuristics, pricer choice by product family | **Yes** |

Two structural rules from the blueprint:

- **Inheritance.** A base institutional config is specialized by an overlay without duplicating
  the tree. `core.config.load_config(path, base=...)` already does this (deep-merge, overlay
  wins). This *is* the profile mechanism (below).
- **One home for environment vs economics.** Runtime/environment settings never enter the
  reproducibility hash; economic settings always do. Mixing them is the trap — it makes a
  storage-path change look like an economic change.

Worked example (`qc.yaml`, straight from the blueprint):

```yaml
# qc.yaml
version: "2026.06"          # blueprint Part I: every config set is versioned
quote_filters:
  max_spread_pct: 0.25
  max_quote_age_seconds: 60
  min_open_interest: 10
  require_positive_bid: true
iv_solver:
  lower_vol: 0.0001
  upper_vol: 5.0000
  price_tolerance: 1.0e-6
  max_iterations: 100
```

### Layer 2 — typed, validated config objects

YAML is data; compute must not touch it. Between them sits one typed object per domain. The
template already exists in the canonical tree (`risk/config.py`, `core/config/platform_config.py`,
and `infra-saxo`'s `SaxoConfig`): a frozen dataclass that

1. carries a `version: str` (blueprint Part I mandates versioning each set),
2. is built by a `from_config(mapping) -> Config` classmethod (the only YAML→object seam), and
3. validates in `__post_init__`, raising a labelled error on an out-of-range field.

Worked example of the seam:

```python
@dataclass(frozen=True, slots=True)
class SolverConfig:
    version: str
    vol_min: float
    vol_max: float
    iv_tolerance: float
    max_iterations: int

    def __post_init__(self) -> None:
        if not 0 < self.vol_min < self.vol_max:
            raise ValueError(f"vol bracket invalid: {self.vol_min}..{self.vol_max}")

    @classmethod
    def from_config(cls, section: Mapping[str, object]) -> "SolverConfig":
        return cls(version=str(section["version"]), vol_min=float(section["vol_min"]), ...)
```

**The binding rule for compute:** a pure function receives its config object as a parameter
(dependency injection). It never imports a loader, never reads a file, never reaches for a global.
That is what keeps it pure, testable, and deterministic.

### Layer 3 — reproducibility (the hash the team asked for)

This already exists in `algotrading.core` and must be used, not re-built:

- `config_hash(config)` — SHA-256 of **canonical JSON** (sorted keys, fixed formatting), never
  Python's salted `hash()`. Same logical config → same hash on every machine, forever.
- `section_hash(config, section)` + per-section `version` — so "the solver changed" is provable
  without pretending the scenario grid changed.
- `composite_config_hash({component: hash, ...})` — folds **every** config bundle that shaped an
  output into one key. An output shaped by qc + pricing + a per-broker forward config gets a hash
  that moves when **any** of them moves.
- `ProvenanceStamp{ calc_ts, code_version, config_hash, source_records, ... }` — stamped on every
  derived record; `validate_stamp` recomputes the hash and rejects a tampered stamp.

**The binding rule for outputs:** every derived record carries a `ProvenanceStamp` whose
`config_hash` is the **composite** of every config bundle that shaped it. Environment settings
(`environment.yaml`) are **never** in that hash — they are not economics and must not perturb
reproducibility. Recap: same inputs + same `config_hash` + same `code_version` ⟹ byte-identical
output, which is exactly the determinism mandate (blueprint Part IV §F).

## Profiles = the blueprint's inheritance, named

A **profile** is a named bundle of all compute parameters — and it is not new machinery. It is a
base config plus a named overlay (blueprint Part VII inheritance), resolved by the existing
`load_config(overlay, base=...)` into a `LoadedConfig` with one `config_hash`. A run records the
profile's `config_hash` in its stamps, so "which parameters produced this?" has a one-word answer
and the run is reproducible.

| Stage | What a profile is | Status |
|-------|-------------------|--------|
| Now | a directory of YAML in the repo: `configs/default/` + `configs/profiles/<name>.yaml` overlays | target of this work |
| Next | save/load a resolved profile by name (write the merged YAML + its hash out, load it back) | follow-up |
| Later | profiles created/edited via the API and front | after the API layer lands |

The mechanism never changes across these — only where the YAML comes from. Build the overlay +
hash discipline once, now, and the later stages are wiring.

## The strict standard: config vs internal constant

To be enforceable rather than absolutist, the line is:

- **Must be config (YAML + validated object):** anything that drives an economic/compute outcome
  or an IO target — thresholds, windows, tenors, delta bands, `max_expiries`/`strike_window`,
  bumps, solver/SVI bounds and tolerances, scenario grids, QC/anomaly limits, cadences, hosts,
  ports, URLs, paths, timeouts, client-id bands, default symbols/exchanges/currencies.
- **May stay a constant in code:** genuine internal invariants with no economic meaning — math
  constants, ASCII separators, array indices, overflow caps, type sentinels, wire-field name maps.

The test for a literal: *if a desk or an operator might ever want it different, it is config.* When
unsure, it is config.

## Failure modes this prevents

- **Silent divergence.** Ten modules each YAML-ising params their own way is how the two-model
  drift happened; one schema + one loader + one hash function prevents the rerun.
- **Fake reproducibility.** A `config_hash` that omits one bundle (e.g. stamps qc but not pricing)
  brands two different inputs with the same key — use `composite_config_hash`, always.
- **Environment poisoning the hash.** Putting the storage path in an economic config makes a
  deployment move look like a research change. Keep `environment.yaml` out of the hash.
- **Loader deep inside compute.** A function that reads YAML itself is non-deterministic and
  untestable. Config enters only as a parameter.

## What this means for the fix tasks

The current state vs the target (from the audit):

1. **Standardize on YAML.** `configs/default.toml` → YAML, read via the existing
   `core.config.load_config`; retire the `tomllib` path in `loader.py`. (Blueprint and the team
   both want YAML; the TOML is also read nowhere in `infra`.)
2. **Create the six base YAMLs** in the canonical tree (`packages/infra` ships none today; they
   exist only under `Vincent's Code/`). Use the blueprint taxonomy above.
3. **Generalize the typed-config pattern** (`from_config` + `__post_init__` + `version`) to every
   domain that currently hardcodes — qc, solver, surfaces/SVI, forwards, universe/`ChainSelection`,
   connectivity, alerts, anomaly, scenario `roll_down_days`. The audit lists the exact `file:line`.
4. **Wire the validated config into `infra`** at the orchestration entrypoint and thread it as
   parameters — the single highest-leverage fix, since today nothing loads it.
5. **Stamp the composite `config_hash`** on every derived record; keep `environment.yaml` out.

Each becomes one task; they all conform to this doc, which is why this is written first.

## Status

Draft, blueprint-anchored. To be **ratified by a short ADR** once the owner signs off (it codifies
a binding house standard). Cross-cutting decisions the audit surfaced that are not settled here —
e.g. whether the decorative `StorageRepository` port becomes load-bearing or is dropped, and the
on-disk profile format — are tracked in [`.agent/open-questions.md`](../.agent/open-questions.md),
not pre-decided.
