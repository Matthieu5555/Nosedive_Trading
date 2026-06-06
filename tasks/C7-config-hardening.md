# C7 — Config hardening: kill hardcoding, wire validated config, lock reproducibility

> **STATUS (2026-06-06): landed — tasks 1–5 done; gate reopened for new compute.**
> Six Part VII YAML bundles + the bundle-aware `load_platform_config`; every hashed
> economic param (solver vol bracket, SVI bounds, forward-confidence heuristics, scenario
> `roll_down_days`) repatriated into validated typed config and threaded by DI; per-bundle
> `config_hashes` dict on every `ProvenanceStamp`; injected code identity (commit SHA +
> dirty) on the run manifest; per-run config freeze + `validate_manifest`. The owner
> prerequisite (*no new compute until params are in YAML and reproducibility is locked*)
> is met for **replay-a-run**. Two carry-forwards remain, both explicitly staged later or
> operational (non-blocking):
> - **Effective-dated profile store** — ADR 0028's "Next" stage (a runtime metadata
>   store) for resolving "the config in force on day D" to replay a *past day fresh*. The
>   "now" stage it mandates (YAML overlays + per-run manifest freeze) is done.
> - **Operational `broker.yaml` client-id bands / backoff** — not hashed (operational);
>   the YAML documents them, wiring the supervisor to read them is the remaining step.


- **Owns:** `packages/core/src/algotrading/core/config/**`, new `packages/infra/**/configs/*.yaml`,
  the per-domain config objects across `infra/{qc,validation,iv,surfaces,forwards,universe,
  connectivity,orchestration,risk}`, the manifest/provenance reproducibility hardening in
  `packages/core`. Conforms to **[ADR 0028](../.agent/decisions/0028-configuration-and-reproducibility-standard.md)**
  and `documentation/configuration-and-reproducibility.md`.
- **Depends on:** ADR 0028 (accepted). Tasks 1–2 + 5 (loader, YAMLs, repro hardening) can start
  now. Tasks 3–4 (per-domain wiring + the single load-and-inject entrypoint) sequence **after C6**
  — the orchestration entrypoint the config threads through moves there.
- **Blocks:** nothing structurally, but it is the prerequisite the owner set for *adding* the index
  pipeline: no new compute until params are in YAML and reproducibility is locked.
- **State going in:** the no-hardcode rule is in `.agent/conventions.md` but unenforced. `core` has
  **two non-composable config paths** (typed TOML loader, no overlay; untyped YAML overlay loader);
  no `infra` module loads either, so every domain hardcodes. The hygiene audit (2026-06-05) lists
  the exact `file:line` violations.

## Objective

Every business/compute parameter sourced from a validated typed config, hydrated from a versioned
YAML, built once at one named entrypoint and injected (DI) into pure compute; per-bundle
`config_hashes` stamped on every derived record; a run reproducible from its manifest; a past day
replayable through effective-dated profiles. Gate-green at each step.

## What to do (ordered)

### Task 1 — Unify the loader, standardize on YAML *(now)*
1. Make the typed path overlay-capable: `from_config` builds typed objects over a YAML
   `LoadedConfig` (base + one overlay, deep-merge). Retire the TOML `load_config`/`tomllib` path;
   migrate `configs/default.toml` → YAML.
2. Enforce **max one overlay level**; list-valued keys replace wholesale; a `null` deletes an
   inherited key. Test the merge semantics.

### Task 2 — The six base YAMLs *(now)*
3. Create `environment/broker/universe/qc/scenarios/pricing.yaml` in `packages/infra` per the
   blueprint Part VII taxonomy (the qc.yaml keeps the `forward_engine` + `surface` blocks). None
   exist in the canonical tree today.

### Task 3 — Generalize the typed pattern *(after C6)*
4. One reflective `from_config` builder (dataclass fields → object) so the YAML↔dataclass schema
   cannot drift. A bad field raises `ConfigFieldError(section, field, value)` — never bare
   `KeyError`/`ValueError`, never a silent default for an economic field; unknown keys rejected.
5. Apply to every audited hardcode site: qc thresholds, IV solver bounds (`vol_min/max`), SVI
   bounds/tolerances, forward-confidence heuristics, `ChainSelection` (delta band), connectivity
   (client-id bands, backoff, hosts/ports/URLs/timeouts), alerts, anomaly z-bands, scenario
   `roll_down_days`. `version` is a **label only**, not a reproducibility input.

### Task 4 — Wire config in at one entrypoint *(after C6)*
6. Name the single orchestration function that loads config, builds all typed objects, and threads
   them as parameters (blueprint Part XVII `main()` pattern). No module reads YAML deep in compute.

### Task 5 — Lock reproducibility *(now, in `core`)*
7. **Per-bundle `config_hashes`** (a dict) on every `ProvenanceStamp`, not a folded composite
   (blueprint manifest form). Drop `section_hash`/per-section versions as reproducibility inputs.
8. Hardening: hash the *resolved mapping* with int-vs-float pinned per field; strict canonical JSON
   (`allow_nan=False`, normalize `-0.0`); enforce composite **completeness** at write time (stamp
   keys checked against the manifest); add **code identity** = commit SHA + dirty flag beside
   `code_version`.
9. **Profiles as-of:** per-run manifest freeze of the resolved config + `validate_manifest`
   (recompute-and-reject, like `validate_stamp`); effective-dated profile records (`effective_from`)
   so "replay day D" resolves the config in force on D. Git is dev-time only.

## Test surface

Read `tasks/TESTING.md`. Specific:
- A reorder/whitespace/comment change to a YAML leaves `config_hashes` identical; an economic field
  change moves exactly its bundle's hash.
- `10` vs `10.0`, `-0.0` vs `0.0`, and a `NaN` are handled per the hardening rules (first two equal,
  last rejected).
- A producer that forgets a config bundle fails at write time (completeness check), not silently.
- A run replays byte-identical from its manifest alone; a past-day reconstruction resolves the
  effective-dated profile, not today's.
- Every audited `file:line` literal is gone (grep guard); `from_config` rejects a bad field with
  `ConfigFieldError` carrying the value.

## Done criteria

No business parameter is a `.py` literal in the audited sites; the six YAMLs exist and are loaded
through one entrypoint; per-bundle hashes + validated manifest + code identity make a run and a
past day replayable byte-for-byte; root gate green; the two-config-path split is gone.

## Gotchas

- The *standard* lands now (ADR 0028); the *application* (tasks 3–4) waits on C6 so the entrypoint
  isn't built twice.
- Don't reintroduce the divergence: one schema, one loader, one hash discipline — not per-module.
- `environment.yaml` never enters the hashes; but a storage path that encodes a partition component
  (date, venue) is not inert — record it in the manifest's input-partition list.
