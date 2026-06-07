# REP6 — pydantic v2 for the config layer (retire `reflective.py`)

> **READY — largest + riskiest swap (determinism). Split into 6a/6b/6c.**
> ([AUDIT-library-leverage-2026-06-07.md](AUDIT-library-leverage-2026-06-07.md))
> The clearest single "reinventing the library" case in the repo: `reflective.py` re-implements
> pydantic v2 type coercion; `__post_init__`×9 re-implements `Field` constraints.

- **Owns:** `packages/core/src/algotrading/core/config/` — `reflective.py`,
  `platform_config.py`, `loader.py`, `__init__.py`, and `packages/core/tests/test_config_core.py`.
- **Depends on:** nothing external. **Hard internal constraint:** the reproducibility hash
  path (`canonical_json` → SHA-256 `config_hashes`) and per-run config freeze (ADR 0028) must
  stay byte-identical.
- **Blocks:** nothing, but compounds with every nested/keyed config block added later (1J
  registry, 1B Δ-selection, 2B scenario grid already strain the hand-rolled coercer).
- **State going in:** the economic config layer uses frozen `@dataclass(slots=True)` +
  hand-written `__post_init__` + a hand-rolled reflective coercion engine. pydantic is **not**
  a dependency of `packages/core` today. ~330 LOC of hand-rolled validation.

## Objective

Replace the hand-rolled config validation with pydantic v2 **frozen + strict** models, deleting
`reflective.py` and the `__post_init__` range checks — **without changing a single validated
value's bytes** as seen by the reproducibility hash.

## What to do (ordered) — keep the gate + reproducibility tests green at each sub-step

1. **6a — config models.** Convert the 9 `platform_config.py` dataclasses to pydantic v2
   models: `frozen=True`, `model_config = ConfigDict(extra="forbid", strict=True)`,
   `Field(gt/ge/lt/le)` for the range checks, `Literal[...]` for the `*_convention` /
   `*_normalisation` enum-membership checks. **Strict mode is mandatory** so `10.5 → int`
   still rejects (the current `reflective.py` contract). Delete the corresponding
   `__post_init__` bodies.
2. **6b — loader / nesting.** Use native pydantic nested models + `dict[str,int]` fields to
   collapse the `loader.py` escape-hatch builders (`_build_qc_threshold`, `_build_universe`,
   `_coerce_floor`). **Preserve the `indices:` block canonicalization** that feeds
   `config_hashes["universe"]` — keep a `model_validator` that produces the same stable,
   JSON-ready form. Then **delete `reflective.py`.**
3. **6c — error mapping.** Route pydantic `ValidationError` → the existing
   `ConfigFieldError(section, field, value, reason)` at one boundary (the loader), so callers
   and tests see the same structured error. Or accept the new error type and update tests —
   but pick one and keep the section/field semantics.
4. **Prove determinism.** Add/keep a test asserting `config_hashes` for a fixed bundle are
   **byte-identical** before and after the swap. This is the acceptance bar — a changed hash
   is a failure, not a migration.

## Done when

Root gate green; `reflective.py` is gone; config-hash byte-equality test passes; strict
rejection cases (`10.5→int`, bool-as-int, unknown key, missing field) still raise with the
section/field info; per-run freeze + `validate_manifest` unchanged.
