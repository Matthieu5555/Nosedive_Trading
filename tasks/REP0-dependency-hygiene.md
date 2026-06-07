# REP0 — Dependency hygiene & accuracy fixes

> **READY — no blocker.** Cheapest, lowest-risk item in the REP backlog
> ([AUDIT-library-leverage-2026-06-07.md](AUDIT-library-leverage-2026-06-07.md)).
> Three declared libraries have **zero source imports**, and two doc claims are false.

- **Owns:** dependency declarations in `packages/infra/pyproject.toml` and
  `packages/infra-ibkr/pyproject.toml`; the stale docstring in
  `packages/infra/tests/test_risk.py:6`; the "built on pycryptodome" claim in
  [ADR 0031](../.agent/decisions/0031-ibkr-historical-data-cp-rest-oauth1a.md) and
  `packages/infra-ibkr/src/algotrading/infra_ibkr/connectivity/cp_rest_oauth.py:6`.
- **Depends on:** nothing. (The polars line couples to REP2; the pycryptodome line
  couples to REP8 — see below.)
- **Blocks:** nothing on the critical path. Pure hygiene; makes the leverage scorecard honest.
- **State going in:** `pandas`, `polars`, `pycryptodome` are all declared + locked but
  imported by **no source module** (verified by grep). py-vollib and QuantLib are real
  but **test-oracle only** (1 import each), which reads as under-leverage but is correct.

## Objective

Stop paying for plumbing libraries we then hand-roll around, and remove two inaccurate
claims so the next agent isn't misled. No behaviour change.

## What to do (ordered)

1. **Drop `pandas` as a direct dep** in `packages/infra/pyproject.toml` (it stays
   transitively via nautilus — confirm `uv lock` still resolves it). No source imports it.
2. **Resolve `polars`:** if REP2 is being taken, leave it (REP2 is its first real use);
   otherwise drop it. Do **not** leave a phantom dep whose pyproject comment claims it is
   "the core" while no module imports it. Pick one and make the comment true.
3. **Resolve `pycryptodome`** in `packages/infra-ibkr/pyproject.toml`: either remove it now,
   **or** schedule REP8 and keep it. Either way, fix the ADR 0031 / `cp_rest_oauth.py:6`
   text — today the signer is stdlib-only (`hmac`/`hashlib`/`base64`), not "built on
   pycryptodome".
4. **Document py-vollib + QuantLib as test-oracle deps** — a one-line comment in the
   relevant pyproject and a note in `packages/infra/README.md`, so their low import count
   is not mistaken for under-leverage. (They are kept *independent* on purpose: see the
   headline of the audit.)
5. **Fix `test_risk.py:6`** — the module docstring claims a py_vollib cross-check the code
   does not perform (it cross-checks against QuantLib's `BlackCalculator`). Correct the
   docstring, or add the vollib price oracle to make it true.

## Done when

Root gate green (`ruff && mypy && lint-imports && pytest`); `uv.lock` re-resolved; no
declared-but-unimported direct dep remains without a deliberate, commented reason; the two
false claims are gone.
