# CI — run the gate on push/PR (currently manual-only)

> **DONE — archived 2026-06-14.** Landed as `.github/workflows/gate.yml` (commit `d9cd767`,
> M2; `scripts/` folded into the gate at `73e5338`). It *exceeds* this spec: three parallel
> jobs — `python-gate` (ruff+mypy+import-linter+pytest), `smoke` (offline byte-identical
> replay), `web` (npm ci + lint + test) — vs. the two asked for, on `push` and `pull_request`,
> Python 3.13, uv- and npm-cached, no secrets. The "no CI at all" baseline below is the
> pre-build state, kept for history. **Not covered here and split out:** secret/dependency
> scanning — see [platform-secret-and-dep-scan](../platform-secret-and-dep-scan.md).

A **parallel, cross-cutting** slice. The front-page work (1I) is the sprint priority; this
is non-conflicting and exists to protect it: today the root gate is the *only* gate and it
runs **only by hand**, so a regression on `main` or in a PR surfaces only when someone
remembers to run it. Add a CI workflow that runs the existing gate automatically on push
and PR, so nothing in the Mon/Fri multi-agent sprint lands red unnoticed.

- **Owns:** a new `.github/workflows/ci.yml` (GitHub Actions — the remote is
  `github.com/Matthieu5555/Nosedive-Trading`, confirmed via `git remote -v`). Touches **no**
  `packages/**`, `apps/**`, or `core/config/**` code — it only *runs* the gate already
  defined in `AGENTS.md` and `pyproject.toml`, so it collides with nothing in flight.
- **Depends on:** nothing. The gate it runs already exists: `ruff`/`mypy`/`import-linter`/
  `pytest` are dev deps in the root `pyproject.toml` (`[dependency-groups]`); the web app
  verifies with `npm run lint && npm test` in `apps/frontend/web`.
- **Blocks:** nothing structurally — but it is the safety net for parallel agent work
  (D1 storage, C7 carry-forwards, the 1x front tasks all land into the same trunk). Pairs
  with `server-deploy-plumbing.md` (also non-compute, also cross-cutting) and never trips
  the owner's *"no new compute until C7 + reproducibility lock"* gate: CI adds zero compute.
- **State going in (audited 2026-06-07):** there is **no CI at all** — no `.github/` dir, no
  `.github/workflows`, and no other host's config at the repo root (no GitLab/CircleCI/
  Travis/Jenkins/Azure/Drone/Woodpecker file). The gate runs only when a human types it.

## Objective

On every push and every pull request, the **exact** root gate and the **exact** front gate
run automatically on a clean checkout, and a red gate blocks the PR. The CI runs nothing the
manual gate does not, scopes to the same trees, needs no secrets (the gate is offline), and
finishes fast through dependency caching.

## What to do (ordered)

1. **Create `.github/workflows/ci.yml`.** Trigger on `push` and `pull_request`. Keep it to
   **two jobs** that can run in parallel — `python` (the root gate) and `web` (the front
   gate) — rather than one mixed job, so a Python failure and a web failure are read
   independently and each job caches its own ecosystem.
2. **Python job — the root gate, byte-for-byte.** Check out, install `uv` (the
   `astral-sh/setup-uv` action with caching enabled, or install + cache `~/.cache/uv`
   keyed on `uv.lock`), pin Python **3.13** (matches `target-version = "py313"` /
   `mypy target`), `uv sync`, then run the gate as one step exactly as `AGENTS.md` states:
   `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`.
   Do **not** re-implement scoping in the workflow — `ruff`/`mypy`/`pytest` already exclude
   the read-only reference checkout, `notebooks`, `scripts`, and scratch dirs via
   `pyproject.toml` (`extend-exclude`, `mypy` excludes, pytest config). The doc-freshness
   test (`packages/infra/tests/test_doc_freshness.py`) already runs under pytest, so CI gets
   it for free — do not special-case it.
3. **Web job — the front gate.** Check out, set up Node (the `actions/setup-node` action
   with `cache: npm` and `cache-dependency-path: apps/frontend/web/package-lock.json`),
   then in `apps/frontend/web` run `npm ci && npm run lint && npm test`. `package-lock.json`
   is present, so `npm ci` is correct (reproducible, lockfile-faithful — not `npm install`).
   `scripts` are `lint` → `eslint .` and `test` → `vitest run` (already non-interactive).
4. **Cache both ecosystems** so reruns are fast: uv cache keyed on `uv.lock`; npm cache via
   the setup-node built-in keyed on the web lockfile. No other caches needed.
5. **No secrets.** The gate is fully offline; do not add `env`/`secrets` blocks, broker
   creds, or network steps. If a step ever needs the network, it does not belong in the gate.
6. **Branch coverage is optional, non-blocking.** `uv run pytest --cov` is a *separate*
   deliberate step (`AGENTS.md`), not the gate. If added at all, add it as a non-required
   job (or omit it) — it must never gate a PR.

## Test surface

This task ships a workflow, not code, so its "tests" are the CI runs themselves:
- A push to a branch and an opened PR each trigger both jobs; both go **green** on a clean
  tree (the gate already passes locally — confirm with the manual command before pushing).
- A deliberately broken lint/type/import/test (Python) **or** a broken eslint/vitest (web)
  turns the relevant job **red** and the other stays green — proving the two-job split and
  that CI runs the real gate, not a stub.
- The workflow runs with **no** repository secrets configured (offline gate).
- A second run on an unchanged tree is materially faster than the first (caches hit).
- `actionlint` (or a YAML lint) is clean on `ci.yml` — no malformed workflow.

## Done criteria

`.github/workflows/ci.yml` exists; push and PR each run the root gate
(`uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`, Python
3.13, uv-cached) and the front gate (`npm ci && npm run lint && npm test` in
`apps/frontend/web`, npm-cached) as two parallel jobs; the scope matches the manual gate
exactly (no re-implemented includes/excludes — it inherits `pyproject.toml`); no secrets;
coverage is optional and non-blocking; a red gate blocks the PR; the workflow lints clean.

## Gotchas

- **Don't re-specify the gate's scope in YAML.** The exclusions (reference checkout,
  `notebooks`, `scripts`, scratch) live in `pyproject.toml` and the test config. Re-listing
  them in the workflow is the divergence that lets CI and the manual gate drift apart — run
  the same commands from the repo root and let the config decide scope.
- **`npm ci`, not `npm install`** — CI must honour `package-lock.json` exactly; `npm install`
  can mutate the lockfile and mask a dependency drift the gate should catch.
- **Pin Python to 3.13.** ruff `target-version` and the mypy config assume it; letting CI
  pick a different interpreter can hide or invent type/lint findings.
- **The remote is GitHub** (`Matthieu5555/Nosedive-Trading`) — hence GitHub Actions and
  `.github/workflows/`. If the host ever changes, the gate commands are portable; only the
  workflow wrapper would be rewritten for the new host.
- **Keep it offline and secret-free.** The whole point of the gate is that it is
  deterministic and needs no credentials; a network step would make CI flaky and is a sign
  something that isn't part of the gate is leaking in.
- **Coverage is not the gate.** `pytest --cov` is slower and deliberately separate — if it
  ever becomes a required check, you've changed what the gate *is*, which is an `AGENTS.md`
  decision, not a CI-config decision.
