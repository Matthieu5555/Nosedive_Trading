# V1 — End-to-end verification & smoke test: prove the whole pipeline runs, through the front

> **Parallel, cross-cutting safety net.** This is *not* a re-run of the unit/contract suite. It is one
> fast, repeatable, full-stack walk — "is the whole stack alive on real (or replayed) data, all the
> way through the front" — complementary to the granular tests. The front (1I) is the priority; V1 is
> built and extended incrementally as each stage lands, so it is the green light for the Fri
> 2026-06-12 "platform functional" milestone.

- **Owns:** a new top-level driver `scripts/smoke_e2e.py` (run via `uv`) plus any small fixtures it
  needs, sitting beside the existing `scripts/{ibkr_bootstrap,reconstruct_sample,export_sample}.py`.
  It owns **no** compute — it only drives existing public entrypoints and asserts. Conforms to
  **[ADR 0028](../.agent/decisions/0028-configuration-and-reproducibility-standard.md)** (provenance
  + `config_hashes`) and the acceptance criteria the four headline tests already encode.
- **Depends on:** usable end to end once **1C** (capture/replay), **1F** (projected grid), and **1I**
  (the four new BFF routers) land — but scaffold **now** against what exists: the SAMPLE provider
  replay (`apps/frontend/src/algotrading/frontend/providers.py`, the committed
  `synthetic_known_answer` chain), `orchestration.run_end_of_day` /
  `orchestration.reconstruction.reconstruct_day`, and the six real BFF routers already wired in
  `apps/frontend/src/algotrading/frontend/app.py`. Each later stage is added to the same driver as it
  arrives. Cross-refs: `1C-capture-daily-close-and-history.md`, `1F-analytics-projection.md`,
  `1H-qc-index-grid.md`, `1I-front-page.md`, `D1-storage-foundation.md`,
  `archive/C7-config-hardening.md`.
- **Blocks:** nothing structurally. It *gates the milestone* — a red V1 means the platform is not
  "functional" regardless of unit-test colour.
- **State going in (verified 2026-06-07):** `run_end_of_day()` exists
  (`infra/orchestration/pipeline.py`) and `reconstruct_day()` exists
  (`infra/orchestration/reconstruction/batch.py`). The BFF (`app.py`) wires six routers —
  `health` (`/api/health`), `surfaces` (`/api/surfaces`), `risk` (`/api/risk`), `run` (`/api`),
  `config` (`/api/config`), `oauth` (`/api/oauth`) — over a real `ParquetStore`; the SAMPLE provider
  replays committed events through the actor. The web front lives at `apps/frontend/web` (Vite +
  React 19, vitest; scripts `build`/`test`/`lint`), with component tests under `src/pages/*.test.tsx`.
  There is **no** end-to-end driver script today. The C4-deleted `/api/market` and `store_serving.py`
  are gone — do not reference them.

## Objective

One command — `uv run python scripts/smoke_e2e.py` — that exercises the real path end to end and
emits a single PASS/FAIL summary with one line per stage and a process exit code. It walks the actual
stack, not mocks: a deterministic day in, a projected grid out, the BFF serving that grid over HTTP,
and the web front building and passing its component tests. It asserts the cross-cutting invariants
that the headline acceptance tests encode (byte-identical replay, provenance, reconstruction,
handover), that provenance stamps + per-bundle `config_hashes` are present (ADR 0028), and that there
is no look-ahead (as-of reads only). It is fast enough to run on every branch and deterministic enough
to run twice with the same result. Default data source is the offline SAMPLE replay so the smoke
needs no network, no broker, and no entitlement; a `--provider`/`--date` flag can point it at a real
captured day once 1C lands.

## What to do (ordered)

Build the driver as independent stages, each emitting one `[PASS]`/`[FAIL]`/`[SKIP]` line and
contributing to the worst-case exit code. A not-yet-landed stage (e.g. an unbuilt 1I router) reports
`[SKIP]` with a reason rather than failing, so the script is useful from today and tightens as each
stage arrives. Drive only documented public entrypoints — never reach into compute internals.

1. **Stage 0 — bootstrap.** Load config once via `load_platform_config(configs/)` (the C7 single
   entrypoint), open a `ParquetStore` on a scratch data root (a temp dir the script owns and cleans
   up, or a `--data-root` flag), and bind one `correlation_id` for the whole run. Fail hard here if
   config or the store will not open — nothing downstream is meaningful.

2. **Stage 1 — capture / deterministic replay (1C / actor).** Produce one day of raw events into the
   `ParquetStore`. Default: the SAMPLE provider replay (the committed `synthetic_known_answer` chain
   driven through the one `RawCollector` / actor seam), which is offline and deterministic. With
   `--provider`/`--date`, replay a real captured day instead. Assert the raw partition landed under
   the ADR-0034 §4 provider-partitioned layout. This is the **deterministic replay** the rest of the
   smoke stands on — same input, same bytes.

3. **Stage 2 — analytics pipeline (1F).** Run the end-of-day analytics over the replayed day:
   `run_end_of_day(store, trade_date=…, correlation_id=…, clock=…, stages=…)` (or
   `reconstruct_day` for a pure reconstruction path), driving forwards → IV → surfaces → pricing →
   risk and the 1F projection. Assert the projected `(tenor × delta-band)` grid was produced
   (`ProjectedOptionAnalytics` rows, the pinned eight tenors, the 30Δ-put→ATM→30Δ-call window) and
   that each cell carries **both** Greek representations — decimal per-unit **and** dollar, each
   dollar number unit-tagged (P0.2 / ADR-0029 `dollar_*` names). `[SKIP]` the grid assertions if 1F
   has not landed yet, but still assert the surface/risk outputs that exist today.

4. **Stage 3 — BFF over HTTP (existing + 1I).** Start the FastAPI app (`create_app`) against the same
   store — in-process via `TestClient`, or a real `uvicorn` on a loopback port — and `GET` every
   endpoint, asserting **real store-backed JSON, never a 500**: the existing `health` (`/api/health`),
   `surfaces` (`/api/surfaces`), `risk` (`/api/risk`), `run`/providers (`/api`), `config`
   (`/api/config`); and the four new 1I routers — `price-history` (`/api/price-history`),
   `constituents` (`/api/constituents`), `analytics` (`/api/analytics`), `recorded-dates`
   (`/api/recorded-dates`). For the new routers, assert the payload reads back the **same** rows the
   pipeline just wrote (the surface grid + dollar Greeks for `analytics`, the as-of basket for
   `constituents`, the OHLC bars for `price-history`, the captured `trade_date`s for
   `recorded-dates`). `[SKIP]` any 1I route not yet registered in `app.py`.

5. **Stage 4 — web front headless (npm).** From `apps/frontend/web`, run `npm ci`/`npm install` (if
   `node_modules` is absent), then `npm run build` (the `tsc -p tsconfig.json && vite build` script)
   and `npm test` (`vitest run` over `src/pages/*.test.tsx`). Assert both exit 0. This is a build +
   component-test gate, not a browser drive — the front must compile and its component tests must pass.
   `[SKIP]` cleanly with a clear line if `npm`/Node is unavailable on the box.

6. **Stage 5 — cross-cutting invariants.** Assert the safety properties the smoke exists to guard,
   reusing the existing assertions where possible rather than re-deriving:
   - **Provenance + config_hashes (ADR 0028):** every derived record from stage 2 carries a
     `ProvenanceStamp` with a non-empty per-bundle `config_hashes` dict, and a run manifest validates
     (`core.manifest.validate_manifest` recompute-and-reject).
   - **Byte-identical replay:** stage 1→2 run twice over the same input yields byte-identical derived
     output (the property `test_replay_byte_identical.py` / `test_nautilus_replay_byte_identical.py`
     encode) — the smoke asserts the headline result, not a re-implementation.
   - **Reconstruction:** a past day reconstructs to the same compute as live
     (`test_replay_reconstruction.py`).
   - **Handover:** the documented operator path still runs end to end
     (`test_handover_e2e.py`) — the smoke is the runnable, milestone-facing echo of that test.
   - **No look-ahead:** the BFF as-of reads (constituents/analytics as-of the picked date) never see a
     future member or a later snapshot; run the `check-lookahead-bias` skill over the driver's read
     path and assert the as-of resolver, not today's, backs the answers.

7. **Summary + exit code.** Print a single block: one line per stage (`[PASS]`/`[FAIL]`/`[SKIP]` +
   stage name + a one-clause why), then a final verdict line. **Mirror `scripts/ibkr_bootstrap.py`'s
   0/1/2 convention** via `sys.exit(main())`: `0` = healthy (every required stage PASS, SKIPs
   allowed); `1` = hard failure (bootstrap/replay/analytics/BFF down — the stack is broken);
   `2` = soft failure (the spine is alive but a non-blocking stage degraded — e.g. the web build was
   skipped for a missing Node, or a not-yet-landed 1I route). ASCII-only output (the
   `ibkr_bootstrap.py` cp1252 rule). A `--json` flag emits the same verdict as machine-readable lines
   for CI.

## Test surface

Read [TESTING.md](TESTING.md). V1 is itself a test artifact, so the surface is light and mostly about
the driver being honest:
- `test_smoke_exit_code_convention` — the summary maps to `0/1/2` exactly as documented: all-PASS→0,
  a forced hard-stage failure→1, a forced soft-skip→2 (drive with injected stage stubs).
- `test_smoke_is_deterministic` — two consecutive offline (SAMPLE) runs produce the identical verdict
  and the identical derived bytes (the smoke must not be flaky to be a gate).
- `test_smoke_offline_needs_no_network` — the default run touches no broker/network/entitlement (the
  SAMPLE replay path); a sandbox with no egress still goes PASS.
- `test_smoke_skips_unlanded_stages` — with a 1I router absent from `app.py`, that stage is `[SKIP]`
  with a reason and the verdict is `2`, not `1` (the script is useful before 1I lands).
- `test_smoke_asserts_no_500s` — every probed BFF endpoint returns a non-5xx, store-backed body; a
  router wired to raise turns the stage red.
- Do **not** duplicate the granular suites — V1 asserts the *headline* result of the four acceptance
  tests by reference, it does not re-implement byte-comparison, provenance recompute, or look-ahead
  detection from scratch.
- Gate green: `uv run ruff … && uv run mypy … && uv run lint-imports && uv run pytest` for the driver
  + its tests; `npm run build && npm test` (in `apps/frontend/web`) for the front stage it shells out
  to.

## Done criteria

`uv run python scripts/smoke_e2e.py` walks the real stack — bootstrap → deterministic replay (1C) →
analytics + projected `(tenor × delta-band)` grid with decimal **and** unit-tagged dollar Greeks (1F)
→ all BFF endpoints (the existing six + the four 1I routers) returning store-backed JSON with **no
500s** → the web front building and its component tests passing (npm) — and emits a single PASS/FAIL
summary with one line per stage. It asserts provenance stamps + per-bundle `config_hashes` (ADR 0028),
echoes the four headline acceptance results (byte-identical replay, provenance, reconstruction,
handover), and confirms no look-ahead (as-of reads). It runs offline by default, is deterministic
across two runs, `[SKIP]`s not-yet-landed stages instead of failing, and exits `0/1/2` on the
`ibkr_bootstrap.py` convention. Root gate green.

## Gotchas

- **Not a unit-test re-run.** If V1 starts re-deriving byte-comparisons or re-detecting look-ahead, it
  has drifted into the granular suite's job. It drives public entrypoints and asserts headline
  results — depth lives in `packages/*/tests/`.
- **Offline by default.** The SAMPLE provider (`providers.py`, the `synthetic_known_answer` chain) is
  the default source precisely so the smoke needs no network or broker; `--provider`/`--date` opts
  into a real captured day once 1C is producing them. A smoke that needs a live Gateway is not a smoke.
- **Do not resurrect deleted code.** `/api/market`, `/api/orders`, and `store_serving.py` were removed
  in C4 — V1 probes the six real routers + the four 1I routers, nothing under `/api/market`.
- **`[SKIP]` is a first-class outcome, exit 2.** Build the driver so an unlanded stage degrades the
  verdict to soft-failure with a reason, never a hard crash — that is what makes V1 buildable now and
  incrementally tightened. Reserve exit `1` for the spine actually being broken.
- **Scratch store, cleaned up.** Run against a temp/`--data-root` the script owns so the smoke never
  writes into a real data root or leaves partitions behind; the partition layout it asserts is the
  ADR-0034 §4 provider-partitioned one (D1).
- **Determinism discipline (C7 hardening).** Two offline runs must match byte-for-byte without
  `PYTHONHASHSEED` reliance — the same `-0.0`/`10` vs `10.0`/`NaN` rules the stamp hash enforces apply
  to anything the smoke compares.
- **ASCII-only output.** Follow `ibkr_bootstrap.py` — a non-ASCII char on a cp1252 console raises
  `'charmap' codec can't encode`; keep the summary plain.
- **`uv` for Python, `npm` for web.** No bare `python`/`pip`; the front stage shells to `npm` in
  `apps/frontend/web` and must surface its exit code into the verdict.
