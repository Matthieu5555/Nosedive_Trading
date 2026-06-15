# platform-secret-and-dep-scan — close the orphaned secret + dependency scan gap
<!-- state: DONE 2026-06-15 — branch worktree-agent-a379a1bf2eedc327a; see TASKBOARD for handoff note -->

A **parallel cross-cutting** slice. The no-secrets rule (`AGENTS.md`) is **convention-only**:
there is no scan that fails a build when a credential or an abandoned/CVE-ridden package slips
in. Two earlier tasks each disowned this: the CI spec scoped itself to "no secrets, the gate is
offline" and added none, and the security-review spec said adding a scanner "is the CI task's
job" and cross-referenced back. Neither shipped it. This task owns it outright so it stops
falling between them.

- **Owns:** the secret-scan + dependency-vuln **gate jobs** — either two jobs added to
  `.github/workflows/gate.yml` or one new `.github/workflows/scan.yml`, plus a
  `.pre-commit-config.yaml` (or a `gitleaks`/`detect-secrets` config) at the repo root for the
  local pre-commit path. Touches **no** `packages/**`/`apps/**` compute — it only runs scanners
  over the tree and the lockfile.
- **Depends on:** nothing. `gate.yml` already exists (see
  [archive/platform-ci-pipeline](archive/platform-ci-pipeline.md)); this extends it. `.env` is
  gitignored with the single `!.env.example` exception; `uv.lock` is the resolved dependency set
  to audit.
- **Blocks:** nothing structurally, but it is a named **gate** in the
  [platform-security-review](platform-security-review.md) report (that review recommends the
  scanner as a finding; this task *builds* it). The 3B live-order gate is cleaner to open with
  this green.
- **State going in (audited 2026-06-14):** no `.pre-commit-config.yaml`, no `.gitleaks.toml`, no
  `.secrets.baseline`, no `pip-audit` step anywhere; `gate.yml` runs ruff/mypy/import-linter/
  pytest + smoke + web only. `git ls-files` exposes no secret today (`.env.example` is the only
  tracked `.env*` and carries placeholders) — so this is a *guard that keeps that true*, not a
  cleanup.

## What to do (ordered)

1. **Secret scan in CI.** Add a job that runs a maintained scanner (`gitleaks` action or
   `detect-secrets`) over the full tree on `push` and `pull_request`. A hit fails the job. Allowlist
   the known placeholders in `.env.example` explicitly (so the template never trips it) — and
   nothing else.
2. **Dependency-vuln scan in CI.** Add a job that runs `uv run pip-audit` (or `pip-audit` over the
   exported `uv.lock`) and fails on any advisory with a fix available; advisories with no fix are
   reported, not gating, with a short allowlist file so a reviewed-and-accepted advisory does not
   re-break the build silently. Keep it offline-friendly — if the advisory DB needs the network and
   CI is offline, pin the DB or mark this job non-blocking and say so in the workflow comment.
3. **Local pre-commit hook.** A `.pre-commit-config.yaml` wiring the same secret scanner so a
   credential is caught *before* it reaches a commit, not only in CI. Document the one-line
   `pre-commit install` in the repo `README.md` (or `AGENTS.md` "House rules" if the owner prefers).
4. **pyCrypto guard.** Fail if `uv.lock`/any `pyproject.toml` ever names the abandoned `pycrypto`/
   `pyCrypto` (ADR 0031 mandates `pycryptodome`). A grep step is enough; pin it so the OAuth-signing
   module, when it lands, cannot pull the wrong package.

## Test surface

Ships workflow + config, so its tests are the runs themselves:
- A branch with a planted fake secret (e.g. an `AWS_SECRET_ACCESS_KEY=`-shaped string) turns the
  secret job **red**; removing it goes green. `.env.example` placeholders stay green.
- A pinned known-vuln dependency turns the dep job red; the clean `uv.lock` is green.
- A planted `pycrypto` line fails the guard.
- The existing three gate jobs are unaffected (no scope change to the real gate).

## Done criteria

CI fails on a real leaked secret, on a fixable dependency advisory, and on `pyCrypto`; a local
pre-commit hook catches secrets before commit; `.env.example` and the clean tree stay green; the
allowlists are explicit and reviewed, not blanket. The [platform-security-review](platform-security-review.md)
"recommend a CI secret-scan" finding can be marked built.

## Gotchas

- **Don't gate on unfixable advisories** — a CVE with no released fix would wedge every PR. Report
  those; gate only on advisories with an available fixed version.
- **Keep the real gate's scope untouched.** This is *additional* jobs; do not fold scanning into the
  ruff/mypy/pytest job where a scanner-DB hiccup could mask a genuine gate failure.
- **Allowlist narrowly.** A broad secret-scan allowlist is how a real leak later hides behind the
  placeholder exception — list the exact `.env.example` lines, nothing wildcarded.
