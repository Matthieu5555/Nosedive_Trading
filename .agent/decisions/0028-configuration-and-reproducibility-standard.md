# 0028 — Configuration & reproducibility standard: YAML → typed config → per-bundle hashes, as-of profiles

- **Status:** accepted, 2026-06-05; **implemented (C7 complete) 2026-06-06** — all five
  staged pieces landed, including the as-of profile store (the "Next" stage built early on
  SQLite behind a `ProfileRepository` port, since the storage-port pattern already existed).
  Ratifies
  [`documentation/configuration-and-reproducibility.md`](../../documentation/configuration-and-reproducibility.md)
  as a binding house standard, and resolves **OQ-5** and **OQ-6** (`.agent/open-questions.md`).
- **Date:** 2026-06-05
- **Implements:** blueprint **Part VII** (configuration), **Part I** (determinism + versioning),
  **Part XIII App. B / Part XV** (the manifest, per-bundle config hashes). The blueprint is the
  authority (ADR 0011); this ADR adds *no* rule the blueprint does not mandate.
- **Relates to:** [[0019-one-immutable-raw-model]] + provenance, [[0015-storage-repository-port-tiered-backends]]
  (the storage port OQ-5 keeps), [[0017-provider-dimension]], [[0027-collection-seam-push-canonical]]
  (the entrypoint the config wiring sequences behind).

## Context

The 2026-06-05 hygiene audit found the no-hardcode rule is written in `.agent/conventions.md` but
**not enforced**: a typed config object exists in `core` yet no `infra` module loads it, so every
domain fell back to `.py` literals. The owner asked for a config/best-practice standard that bans
hardcoding, puts every parameter in editable YAML, supports saved "profiles", and — the team's
explicit requirement — gives **perfect reproducibility**, including replaying a past trading day.

A draft standard was reviewed by three external expert agents (advise + contradict). They found
real defects, corrected here, so the ADR ratifies the *corrected* standard, not the draft:

- The draft described a loader that does not exist (it conflated two non-composable paths: a typed
  TOML loader with no overlay, and an untyped YAML overlay loader). Unifying them is task 1.
- The draft made a single `composite_config_hash` the canonical per-record key. The **blueprint
  manifest carries per-bundle `config_hashes` (a dict)** — the composite was a divergence.
- Real reproducibility holes (int/float hashing, `NaN`/`-0.0`, composite completeness by discipline
  not enforcement, `code_version` defeated by a dirty tree, an unvalidated manifest snapshot).

## Decision

1. **The standard in `documentation/configuration-and-reproducibility.md` is binding.** No
   business/compute parameter is a `.py` literal; it comes from a validated typed config object,
   hydrated from a versioned YAML file (blueprint Part VII taxonomy: `environment/broker/universe/
   qc/scenarios/pricing`), built **once at one named orchestration entrypoint** and threaded as
   parameters (DI) into pure compute. `from_config` failures raise a labelled `ConfigFieldError`;
   no silent defaults for economic fields.

2. **Per-bundle `config_hashes` are the canonical reproducibility key** (blueprint manifest form),
   not a folded composite. The cryptographic core (canonical-JSON SHA-256, `validate_stamp`) is
   kept; the hardening (strict JSON, hash the resolved mapping with int/float pinned, completeness
   enforced structurally, code identity = commit SHA + dirty flag) is mandatory, captured as
   done-criteria in `tasks/C7-config-hardening.md`.

3. **Config is as-of (OQ-6 resolved).** Reproducibility includes replaying a **past day**, so
   profiles carry the platform's point-in-time discipline (as market data and index membership do):
   - **Per-run freeze** — each run's *manifest* stores the fully-resolved config + its hashes,
     validated; a run replays from its own manifest. **Git is dev-time only; the manifest is the
     run-time system of record.**
   - **As-of resolution** — profiles are **effective-dated** in a runtime config store; "replay
     day D" resolves the config in force on D.
   - **Profiles are content-addressed + effective-dated** (a name → append-only versions; a run
     pins an immutable hash). Stage: YAML overlays + manifest freeze now → a metadata store (the
     SQLite "higher layer", not git) → API/front CRUD. Same model throughout.

4. **OQ-5 resolved — keep the `StorageRepository` port, make it load-bearing.** Storage follows
   Vincent's blueprint-aligned architecture: raw in **`.parquet`** (course mandate), DuckDB as a
   query layer later, SQLite for higher layers later — a real multi-backend future, which is what
   the port (already accepted in [[0015-storage-repository-port-tiered-backends]]) exists for.
   Type infra/orchestration/host signatures against the port; widen it only where a caller has a
   legitimate uncovered need; never delete it.

## Alternatives considered

- **Single composite hash per record.** Rejected: diverges from the blueprint's per-bundle manifest
  and hides which bundle changed; a composite may exist only as a derived convenience.
- **Profiles as git-tracked YAML, no runtime store.** Rejected (owner): git is dev-time, not
  run-time, and gives no as-of resolution for replaying a past day. The per-run manifest freeze +
  effective-dated store is required.
- **Per-section sub-hashing + author-maintained section versions as reproducibility inputs.**
  Rejected as over-engineering and unsafe: per-bundle hash already proves change; `version` is a
  human label, the hash is authoritative.

## Consequences

- The config work has one home: `tasks/C7-config-hardening.md` (5 ordered tasks). The standard
  lands now; application (wiring config into `infra`) sequences **after C6** (the orchestration
  entrypoint moves there).
- The two-config-path split in `core` is closed by C7 task 1 (typed `from_config` over a YAML
  overlay; TOML retired).
- Every per-domain hardcoding fix from the audit conforms to one schema/pattern — no re-run of the
  two-model drift on the config surface.
- Reproducibility becomes auditable end to end: per-bundle hashes + a validated manifest snapshot +
  code identity + effective-dated profiles ⟹ a past day is replayable byte-for-byte.
