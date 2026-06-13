# Configuration & reproducibility — architecture and standard

This doc is **architecture + binding standard** (reference, with explanation). It defines how
every parameter in the platform is sourced, validated, and made reproducible — including
**replaying a past day** — and it is the rule the per-domain config tasks must conform to so they
cannot diverge.

For: anyone writing or reviewing code that has a tunable number, threshold, window, bound,
cadence, or path — i.e. almost everyone. It assumes the `algotrading.core` config + provenance
modules as built (and names, below, where they fall short of this target today).

**Authority.** This implements **blueprint Part VII (`documentation/blueprint/07-configuration.md`)**
and the versioning/determinism conventions in **blueprint Part I** (`01-architecture.md` §§ on
determinism and naming) and the manifest in **Part XIII App. B** / **Part XV** (data governance).
The blueprint is the source of truth (ADR 0011); where this doc and the blueprint disagree, the
blueprint wins and this doc is the bug. Ratified by **ADR 0028**.

## The rule, and why

Blueprint Part VII, verbatim in intent: *"Configurations are economic inputs. They should never
live as scattered constants in notebooks or inside implementation files. Every threshold, bump
size, scenario grid, strike-selection rule, and cadence should be represented in a versioned
configuration artifact."*

So the standard is one sentence: **no business/compute parameter is a literal in a `.py` file.**
It comes from a validated config object, hydrated from a versioned YAML file, and the config's
per-bundle hashes are stamped onto every output it shaped — which is what lets a computation be
reproduced and checked byte-for-byte. The audit found this rule is written in `conventions.md`
but not enforced: the typed config exists yet no `infra` module loads it, so every domain fell
back to literals. This doc is the target state that closes that.

## Current state — two config paths that must become one

**This is the first thing to fix, and the doc is honest that the unified path does not exist yet.**
Today `algotrading.core` has **two non-composable** config mechanisms:

| Path | File | Has | Lacks |
|------|------|-----|-------|
| Typed | `core/config/loader.py` → `PlatformConfig` | typed dataclasses, `__post_init__` validation, per-section `version` + hashes | reads **TOML** (`configs/default.toml`); **no overlay**; loaded by no `infra` module |
| Overlay | `core/config/yaml_config.py` → `LoadedConfig` | YAML, base+overlay deep-merge, `config_hash` | **untyped** (raw mapping); no `version`, no `from_config`, no validation |

You get *typing* (TOML, no overlay) **or** *overlay* (YAML, no typing) — never both. So the
"typed object resolved from a YAML overlay with a hash" this doc mandates is the **target**, not
the present. Task 1 below builds it: route the typed `from_config` over a `LoadedConfig.data`
(YAML + overlay), retiring the TOML path.

## Architecture — three layers (target)

```text
   VERSIONED YAML (in repo, blueprint Part VII taxonomy)        ENVIRONMENT (NOT hashed)
   universe.yaml  qc.yaml  pricing.yaml  scenarios.yaml         environment.yaml
   broker.yaml          (+ a profile overlay, effective-dated)  (host/port, log level — inert)
        |                                                              |
        v  load (base + overlay) → deep-merge → LoadedConfig.data      v  injected as-is
        |                                                        runtime wiring (no hash)
        v  per domain: from_config() + __post_init__ validation
   TYPED config objects  ── never read by compute as raw YAML
   UniverseConfig · QcThresholdConfig · SolverConfig · SurfaceConfig · ScenarioConfig · …
   each carries: version (label) ; each bundle yields one config_hash (per-bundle)
        |
        v  built ONCE at the orchestration entrypoint, passed as parameters (DI)
   PURE COMPUTE   snapshots → forwards → iv → surfaces → pricing → risk
        |
        v  derived record + ProvenanceStamp{ code_version, config_hashes{bundle:hash}, sources }
   raw / derived store (.parquet)   ── + the run MANIFEST freezes the resolved config
```

The diagram shows how a parameter travels from a YAML file to a stamped, reproducible output. It
omits the QC and as-of machinery, which wrap compute but do not change this path. Config is built
**once, at one named entrypoint** (the blueprint's `main()` pattern, Part XVII), into typed
objects threaded as parameters — never read from disk deep inside a function. Each derived record
carries the **per-bundle** `config_hashes`; the run's **manifest** freezes the fully-resolved
config (below, Reproducibility). Environment settings travel a separate path and are **excluded
from the hashes** (see the env rule).

### Layer 1 — versioned YAML (blueprint Part VII taxonomy)

Use the blueprint's file set as the canonical base, not an invented scheme:

| File | Owns | In the reproducibility hashes? |
|------|------|--------------------------------|
| `environment.yaml` | host/port, service endpoints, log levels — **inert wiring only** | **No** |
| `broker.yaml` | client-id bands, reconnect/backoff policy, session windows | **No** (operational) |
| `universe.yaml` | underlyings, exchanges, product families, maturity windows, strike-selection (delta band / `ChainSelection`), **capture cadence** | **Yes** |
| `qc.yaml` | quote filters, stale limits, solver thresholds, fit tolerances, anomaly z-bands, the `forward_engine` block | **Yes** |
| `scenarios.yaml` | named stress scenarios, shifts, combinations, report subsets, `roll_down_days` | **Yes** |
| `pricing.yaml` | IV solver bounds, finite-difference bumps, SVI bounds/tolerances, forward-confidence heuristics, pricer choice by family | **Yes** |

Note: `roll_down_days`, the forward-confidence heuristics, the delta-band `ChainSelection`, and the
anomaly z-bands are **extensions** of the blueprint's enumerated fields, not part of Part VII's
literal list — added because the audit found them hardcoded. They follow the same rules.

Two structural rules:

- **Inheritance (blueprint).** A base institutional config is specialized by an overlay without
  duplicating the tree (deep-merge, overlay wins). This is the profile mechanism (below). **Max
  one overlay level** — enforced, not hoped; list-valued keys are *replaced* wholesale by the
  overlay (document this at each list field; a `null` value deletes an inherited key).
- **Environment vs economics — a sharp, decidable rule (not "judge").** *Anything that changes
  which records exist or their values is economic and is hashed; only things that change where or
  how fast bytes move without changing their content are environment.* So capture cadence and
  universe selection are **economic** (they change what is recorded) and live in hashed files; a
  storage path or a log level is environment. A storage path that encodes a partition component
  (a date, a venue) is **not** inert — capture it in the manifest's input-partition list.

Worked example (`qc.yaml`, faithful to the blueprint snippet, including the `forward_engine` and
`surface` blocks the first draft dropped):

```yaml
# qc.yaml
version: "2026.06"                  # blueprint Part I: every config set is versioned (a label)
quote_filters:
  max_spread_pct: 0.25
  max_quote_age_seconds: 60
  min_open_interest: 10
  require_positive_bid: true
forward_engine:
  strike_band_mode: nearest_liquid
  max_candidate_count: 12
  outlier_method: mad
  max_robust_zscore: 3.5
iv_solver:
  lower_vol: 0.0001
  upper_vol: 5.0000
  price_tolerance: 1.0e-6
  max_iterations: 100
surface:
  model: svi
  fallback_model: spline
  min_points_per_slice: 5
  max_rmse: 0.02
```

### Layer 2 — typed, validated config objects

YAML is data; compute must not touch it. Between them sits one typed object per domain. The
template exists (`risk/config.py`, `infra-saxo`'s `SaxoConfig`): a frozen dataclass that

1. carries a `version: str` — a **human label only**, *not* a reproducibility input (the hash is),
2. is built by a `from_config(mapping) -> Config` classmethod (the only YAML→object seam), and
3. validates in `__post_init__`, raising a **labelled custom exception** on a bad field.

The `from_config` failure contract is binding (it was the under-specified seam that let the
two-model drift happen): a missing or out-of-range field raises `ConfigFieldError(section, field,
value)` — never a bare `KeyError`/`ValueError`, never a silent default for an economic field;
unknown keys are rejected, not ignored. To prevent the YAML↔dataclass schema drifting, **drive
`from_config` reflectively from the dataclass fields** (one builder) rather than hand-listing
fields in every domain.

```python
@dataclass(frozen=True, slots=True)
class SolverConfig:
    version: str
    vol_min: float
    vol_max: float
    iv_tolerance: float
    max_iterations: int

    def __post_init__(self) -> None:
        if not 0.0 < self.vol_min < self.vol_max:
            raise ConfigFieldError("solver", "vol_min/vol_max", (self.vol_min, self.vol_max))
```

**The binding rule for compute:** a pure function receives its config object as a parameter
(dependency injection). It never imports a loader, never reads a file, never reaches for a global.
That is what keeps it pure, testable, and deterministic.

### Layer 3 — reproducibility (the hashes the team asked for)

The cryptographic core exists in `algotrading.core` and is sound — keep it: SHA-256 of canonical
JSON (sorted keys) over Python's salted `hash()`, order-canonicalized sources, and `validate_stamp`
recompute-and-reject. Build on it, with these corrections.

**Per-bundle hashes are canonical — not a single composite.** The blueprint manifest (Part XIII
App. B, Part XV) carries a **dict** `config_hashes: {universe, qc, pricing, scenarios, ...}`, one
hash per bundle. Every derived record's `ProvenanceStamp` carries that dict, not one folded scalar.
`composite_config_hash` may exist as a convenience *derived from* the dict, but it is not the
canonical key — making it canonical was a divergence from the blueprint (now removed). Per-section
sub-hashing (`section_hash`) is dropped: per-**bundle/file** version + hash already gives "the
solver changed" granularity (solver params are their own bundle).

**Reproducibility hardening — these are real holes the panel found; they are done-criteria, not
aspirations:**

- **Hash the resolved YAML mapping, not the coerced object,** with explicit numeric normalization,
  and pin int-vs-float per field. Else `10` vs `10.0` hash differently for an identical config.
- **Make canonical JSON strict:** `allow_nan=False`, normalize `-0.0 → 0.0`, reject non-finite
  floats at validation. A "reproducibility hash" must not emit invalid JSON or split `-0.0`/`0.0`.
- **Enforce composite completeness structurally.** A producer declares its full config set in one
  typed object; the stamp's `config_hashes` keys are checked against the manifest at write time, so
  a forgotten bundle is a construction error, not a silent same-hash collision.
- **`code_version` is necessary but not sufficient.** It reads the installed distribution version,
  which a dirty tree or a same-version edit defeats. Stamp a **VCS commit SHA + dirty flag** (or a
  content hash of the importable code) alongside it; an uninstalled `0.0.0+unknown` is a reproduc-
  ibility failure, not a fallback to ignore.

Recap: same inputs + same `config_hashes` + same code identity ⟹ byte-identical output — the
determinism mandate (blueprint **Part I**).

## Reproducing a past day — config is as-of, like everything else

Reproducibility is not only "re-run this run"; it is **replay a past trading day**. Config
therefore carries the same **as-of / point-in-time** discipline the platform already applies to
market data and to index membership (OQ-3, `effective_add/remove_date`). Two mechanisms, both
required:

- **Per-run freeze (replay a run that happened).** Each run's **manifest** stores the fully-
  resolved config plus its `config_hashes`, **validated** like a `ProvenanceStamp` (a hash over the
  stored snapshot must equal the stamped hash; add `validate_manifest`). A run is reproducible from
  its own manifest alone — git is **not** in this path. Git is dev-time (authoring/review of the
  YAML); the manifest is the run-time system of record.
- **As-of resolution (reconstruct a past day fresh).** Profiles are **effective-dated** in a
  runtime config store; "replay day D" resolves the config that was **in force on D**. Without this
  the config would be the one thing in the platform that cannot be replayed through time.

### Profiles — the runtime, effective-dated form of the blueprint's inheritance

A **profile** is a named, effective-dated bundle of all compute parameters: a base config plus an
overlay (blueprint inheritance), resolved to one content-addressed config the manifest can freeze.

| Stage | What a profile is | Notes |
|-------|-------------------|-------|
| Now | YAML overlays + a per-run **manifest freeze** of the resolved config; profile records carry `effective_from` | works immediately; replay-a-run needs only the manifest |
| Next | a runtime config **store**, content-addressed (a profile resolves to a hash; a name → append-only list of versions; a run pins the hash) + effective-dated for as-of resolution | metadata store = the SQLite "higher layer" of the storage direction, not git |
| Later | profiles created/edited via the API/front | same model; only the store backend changes |

Why content-addressed + effective-dated (the panel's improvement on "named mutable profile"):
editing a profile writes a **new** version; a run pins an immutable hash, so it is never silently
mutated; "what ran on D" and "what was in force on D" both have exact answers. The model never
changes across stages — only where the profile bytes live (repo → store → API).

## The strict standard: config vs internal constant

- **Must be config (YAML + validated object):** anything that drives an economic/compute outcome
  or changes which records exist — thresholds, windows, tenors, delta bands,
  `max_expiries`/`strike_window`, bumps, solver/SVI bounds and tolerances, scenario grids,
  QC/anomaly limits, capture cadence, default symbols/exchanges/currencies. Plus IO targets that
  are operational (hosts, ports, paths, timeouts, client-id bands) — in `environment.yaml`/
  `broker.yaml`, **not** hashed.
- **May stay a constant in code:** genuine internal invariants with no economic meaning — math
  constants, ASCII separators, array indices, overflow caps, type sentinels, wire-field name maps.

The test: *if a desk or an operator might ever want it different, it is config.* When unsure, config.

## Failure modes this prevents

- **Silent divergence.** One schema + one named load-and-inject entrypoint + one hash discipline,
  instead of ten modules each YAML-ising their own way (how the two-model drift happened).
- **Fake reproducibility.** A stamp that omits one bundle brands two different inputs alike — hence
  per-bundle `config_hashes` checked for completeness at write time.
- **Untimely reproducibility.** A past day that resolves to *today's* config — hence effective-dated
  profiles + the manifest freeze.
- **Environment poisoning the hash,** or a partition-bearing path masquerading as inert — hence the
  sharp env rule + manifest input-partitions.
- **Loader deep inside compute.** Config enters only as a parameter, built once at the entrypoint.

## What this means for the fix tasks

Tracked as **`tasks/C7-config-hardening.md`**. In order:

1. **Unify the loader + standardize on YAML.** Route the typed `from_config` over a YAML
   `LoadedConfig` (base+overlay); migrate `configs/default.toml` → YAML; retire the `tomllib`
   path. The two-paths split (above) closes here.
2. **Create the six base YAMLs** in `packages/infra` (it ships none; they exist only under
   `Vincent's Code/`), per the blueprint taxonomy.
3. **Generalize the typed pattern** (`from_config` reflective + `__post_init__` + `version` label
   + `ConfigFieldError`) across qc, solver, surfaces/SVI, forwards, universe/`ChainSelection`,
   connectivity, alerts, anomaly, scenario `roll_down_days`. The audit lists exact `file:line`.
4. **Wire config in at one named orchestration entrypoint** and thread it as parameters — the
   highest-leverage fix, since today nothing loads it. Name the function; mirror blueprint Part XVII.
5. **Stamp per-bundle `config_hashes`** on every derived record; freeze + `validate_manifest` the
   resolved config per run; apply the reproducibility hardening (strict JSON, int/float pinning,
   completeness check, code identity); keep `environment.yaml` out.

Sequencing: the *standard* (this doc + ADR 0028) lands now; the *application* (tasks 3–4) sequences
**after C6** unifies the collection seam, since the orchestration entrypoint moves there.

## Status

**Ratified by ADR 0028.** OQ-5 (storage port: keep + load-bearing) and OQ-6 (profiles:
effective-dated content-addressed store + per-run manifest freeze, not git) are resolved — see
[`.agent/open-questions.md`](../.agent/open-questions.md).
