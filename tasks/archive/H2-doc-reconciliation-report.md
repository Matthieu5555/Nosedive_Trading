# H2 — Doc reconciliation report

**Ran against:** commit `e0ab3ab` (settled tree: C7 + broker/notebooks migration
landed), on branch `chore/h1-h2-hygiene-and-docs` (after H1, `fc70f39`). Gate green
throughout, including the new doc-freshness check.

## What was reconciled

### Task 4 — the freshness guard (the keystone, gate-wired)
`packages/infra/tests/test_doc_freshness.py` — stdlib-only, collected by the root
`pytest`. Asserts, mechanically, what the "keep docs alive" rule only asked for by
convention: every `packages/*` and every `infra/**` module dir has a `README.md`;
every `documentation/modules/` symlink resolves; `.agent/map.md` routes every
canonical top-level area (and a *new* canonical top-level dir fails the test until
it is added to the map); no relative markdown link in the map or the package/module
READMEs is dead. 33 cases, green.

### Task 1 — README drift fixes (audited all 18 infra module + 8 package READMEs)
The infra analytics-core and package READMEs were largely accurate post-merge
(Nautilus spine, push collection seam, three adapters all correctly described). Five
concrete drifts fixed — all were docs still claiming a hashed economic parameter was
a `.py` literal, which C7 moved into typed config:
- `forwards/README.md` — the confidence heuristic (`good_rel_residual`,
  `fair_rel_residual`, `full_credit_pairs`, `rel_residual_halflife`,
  `single_pair_confidence`) is `ForwardConfig`, not top-of-file constants; the ghost
  `_MAD_REJECTION_Z` removed (MAD rejection runs through `infra.utils.robust`).
- `iv/README.md` — the vol bracket is `SolverConfig.vol_min/vol_max`, not `_VOL_MIN/_VOL_MAX`.
- `surfaces/README.md` — the SVI feasible box + bound-hit tolerance are `SurfaceConfig`
  (`svi_*_bounds`, `svi_bound_hit_tol`), not `_A_BOUNDS … _BOUND_HIT_TOL`.
- `risk/README.md` — ghost `ROLL_DOWN_DAYS` → `ScenarioConfig.roll_down_days`.
- `execution/README.md` — corrected "top of the layer stack" (`frontend` is the top
  layer, not `execution`).

### Task 2 — catalog refresh (glossary)
Added the merge-introduced terms missing from `.agent/glossary.md`: **Config bundle**,
**Push collection seam / `RawCollector`**, and a note that the config stamp now carries
a per-bundle `config_hashes` dict (C7). No ghost terms found to remove.

### Task 3 — top hops
`.agent/map.md` verified current (already names the Nautilus spine, push seam, C7
bundles, three adapters; routes every top-level area). `documentation/README.md`
(operations handbook) gate command corrected — it omitted `lint-imports`. The
`documentation/modules/` symlink mirror is complete and unbroken (18/18 resolve).

## OQ-7 — raised, then ruled (code conforms to the blueprint)

The data dictionary (`blueprint/09`, authoritative on domain — ADR 0011) named fields
the frozen contracts spelled differently (`forward_price`/`forward`, `implied_vol`/`iv`,
`log_moneyness`/`k`, `scenario_pnl`/`pnl`, `qc_status`/`status`, `dollar_*`/`cash_*`).
Surfaced as OQ-7 rather than silently doc-edited. **Owner ruled (2026-06-06): follow the
blueprint, code conforms, data starts from scratch** — see
[ADR 0029](../.agent/decisions/0029-contract-field-names-conform-to-blueprint.md). The six
persisted-column renames landed across `tables.py`, the validation registry, every
producer/consumer (mypy-enumerated), the scenario serializer key, tests, and the module
READMEs/docstrings. The data dictionary now describes the code exactly — no dict edit was
needed, the code moved to it. `InstrumentKey.broker_contract_id` (dict: `contract_id_broker`)
was left as-is — it's embedded in the canonical key, not a standalone column — flagged as an
optional follow-up. Full root gate green after the rename (mypy 176 files, pytest 0 failures).

## Deliberately not done (scoped out / non-blocking)

- The thin broker-adapter **sub-module** dirs (`infra_*/collectors`, `connectivity`,
  `auth`) have package-root READMEs but no per-sub-dir README. Not required by the
  guard (its scope is `packages/*` roots + the `algotrading.infra` core) and not
  drift — left as-is. Add prose if/when those adapters grow.
- An exhaustive field-by-field data-dictionary↔contracts rewrite is blocked on the
  OQ-7 ruling; doing it now would entrench one side of an undecided naming split.
