# security-review — pre-execution security pass (before any real order send)

> **Parallel cross-cutting review, not a feature.** The front (1I) is the sprint priority; this is
> non-conflicting and gates the one genuinely dangerous path: it must pass **before Phase 3 (3B)
> transmits any real (non-paper) order**. Output is a severity-ranked findings report (`file:line` +
> fix), in the shape of [`archive/H1-repo-hygiene-report.md`](archive/H1-repo-hygiene-report.md) and
> [`archive/H2-doc-reconciliation-report.md`](archive/H2-doc-reconciliation-report.md) — not a code
> change. It produces a verdict; the fixes it names land in the owning tasks.

- **Owns:** nothing structural — a read-only review producing this findings report. It reviews, it
  does not refactor; concrete fixes it surfaces are filed against the owning task (3A/3B, 1I/BFF, the
  IBKR/Saxo auth packages, `.env.example`). Use the repo's `security-review` slash skill as the
  driver and `check-lookahead-bias` only where a finding touches signal/backfill code.
- **Depends on:** **3A/3B existing** to review the order seam — those specs are not yet written
  (the [`execution`](../packages/execution/src/algotrading/execution/__init__.py) package is an empty
  skeleton today). The auth + secrets + BFF half (sections 1, 3, 4, 5 below) can start **now**
  against landed code; the order-seam half (section 2) opens when 3A/3B land. Conforms to
  **[ADR 0024 §4](../.agent/decisions/0024-ibkr-rest-transport-alongside-tws.md)** (read-only
  invariant) and **[ADR 0031](../.agent/decisions/0031-ibkr-historical-data-cp-rest-oauth1a.md)**
  (OAuth 1.0a + pycryptodome).
- **Blocks:** any real (non-paper) order transmission in **3B**. Paper/read-only operation is not
  blocked. The gate is: this report's HIGH/CRITICAL findings are closed (or owner-accepted) before
  the 3B owner flag can flip to live.
- **State going in (audited 2026-06-07):** the dangerous path is **not built yet**, which is why this
  runs ahead of it. `packages/execution` is one docstring line; no `place_order`/`transmit`/`reply`
  call exists anywhere under `packages` or `apps` (grep clean). The IBKR read-only invariant is real
  and tested (`packages/infra-ibkr/tests/test_cp_rest_adapter.py::test_snapshot_is_read_only`). The
  **OAuth 1.0a signing module ADR 0031 mandates does not exist yet** and **pycryptodome is in no
  `pyproject.toml`** — so the dependency check here is partly forward-looking (catch pyCrypto if it
  ever appears) and partly a spec-conformance check on the code that lands. `.env` is gitignored
  (`.gitignore:28-30`, `!.env.example`); `.env.example` is the only tracked `.env*` and carries
  placeholders, not secrets. There is **no automated secret-scan hook** in the repo (no
  pre-commit / gitleaks / detect-secrets config) — the no-secrets rule (`AGENTS.md:95`) is
  convention-only, which is itself a finding to weigh.

## Objective

A single severity-ranked findings report that lets the owner flip 3B to live with eyes open: every
credential-handling path, the order seam's disabled-by-default + owner-gate + audit-log discipline,
the BFF's read-only-for-serving posture, the no-secrets-in-git invariant, and the dependency set are
each reviewed against named files, with each finding carrying `file:line`, a severity, and a concrete
fix. Nothing economic or look-ahead is in scope except where a finding lands on signal/backfill code.

## What to do (ordered)

Drive the pass with the **`security-review`** slash skill; record findings as you go. Plain prose,
`uv` for any check you run (`uv run …`). For each section, cite the concrete files named.

### 1 — IBKR auth: OAuth 1.0a + the CP REST session *(start now)*
1. **The OAuth 1.0a path (ADR 0031).** It is **not implemented yet** — so this is a *spec-conformance
   pre-review* plus a guard. When the signing module lands in `packages/infra-ibkr`, confirm: the
   Live Session Token / consumer key / private key are read from `$HOME`/`.env` (gitignored), never
   committed and never embedded in the app; signing uses **pycryptodome**, not the abandoned
   **pyCrypto** (ADR 0031 §2). Until it lands, the finding is "absent — review on arrival" plus the
   pyCrypto dependency guard (section 5).
2. **The CP REST session + transport (landed).** Review
   [`cp_rest_session.py`](../packages/infra-ibkr/src/algotrading/infra_ibkr/connectivity/cp_rest_session.py)
   and
   [`cp_rest_transport.py`](../packages/infra-ibkr/src/algotrading/infra_ibkr/connectivity/cp_rest_transport.py):
   confirm the docstring claim "No secrets, nothing persisted" holds (the local CP Gateway owns the
   cookie; no auth header is carried). Note `verify_tls` **defaults to `False`**
   (`cp_rest_transport.py:33,39`) — this is **justified** for the self-signed localhost Gateway, but
   the finding must state the invariant it depends on (base URL stays `https://localhost:5000`); flag
   it HIGH **if** any path lets `base_url` point off-localhost while TLS verification stays off.
3. **The read-only invariant (ADR 0024 §4).** Confirm it is enforced *and tested* for every IBKR
   surface: `test_snapshot_is_read_only` asserts the snapshot adapter touches no `order` path. When
   the ADR-0031 history GET lands, confirm the same assertion covers it (the invariant explicitly
   extends to `/iserver/marketdata/history`). A read path that can reach an order endpoint is HIGH.

### 2 — The order seam (3A/3B) *(opens when 3A/3B land)*
4. **Transmission disabled by default.** Confirm 3B sends nothing live unless an explicit owner gate
   is set; the default state is paper/read-only (roadmap Phase 3: "Read-only / paper until an
   explicit owner gate"). A code path that can transmit a live order without the flag is **CRITICAL**.
5. **The owner gate.** Confirm it is two-factor in spirit: a config flag **and** the email-confirmation
   step (3B "Order signing (email) + send"). Confirm the gate is checked at the send boundary, not
   only in the UI, and cannot be bypassed by calling the seam directly.
6. **Audit-log completeness.** Every ticket → sign → send transition is recorded (who/what/when, the
   instrument, the gate state) before the send, append-only. A send with no prior audit record is HIGH.
7. **No credentials in the app.** The order path routes through the existing broker seam
   (Saxo/Deribit/IBKR adapters), never a new ad-hoc path; secrets stay in the adapter packages' `.env`
   (per `packages/infra/.../connectivity/supervisor.py:86`), never in `apps/frontend` or the ticket.

### 3 — The BFF *(start now)*
8. **Read-only-for-serving, with the write exceptions named.** The BFF docstring says it reads only
   down-layer infra seams ([`app.py:5-15`](../apps/frontend/src/algotrading/frontend/app.py)). But it
   is **not GET-only**: CORS allows `["GET", "POST", "DELETE"]` (`app.py:46-50`) and three live
   non-GET routes exist — `POST /api/run`
   ([`routers/run.py:53`](../apps/frontend/src/algotrading/frontend/routers/run.py)),
   `POST /api/oauth/saxo/start` and `DELETE /api/oauth/saxo`
   ([`routers/oauth.py:30,70`](../apps/frontend/src/algotrading/frontend/routers/oauth.py)).
   Adjudicate each: `/api/run` launches a tracked pipeline job (not an order — acceptable, but
   confirm it cannot reach a transmit path); the oauth routes are the Saxo CSRF flow, currently
   failing closed (`501 saxo_backend_not_configured`). Confirm **no route writes a secret or
   transmits an order**, and that the single-operator assumption holds — there is **no multi-user
   auth by design**, so confirm nothing here is exposed to the open internet with a write/secret path.
9. **CORS.** `allow_origins` is one env-driven origin (`FRONTEND_BASE_URL`, default
   `http://localhost:5173`) — not `*` (`app.py:28-29,44-50`). Confirm the prod deploy sets it to the
   real origin and `allow_headers=["*"]` is acceptable given no cookie/credentialed auth.

### 4 — Secrets & config *(start now)*
10. **No secrets in git.** Confirm `.env` and `.env.*` are ignored with the single `!.env.example`
    exception (`.gitignore:28-30`); `git ls-files` returns only `.env.example` (and `Test Lenny/`'s,
    which is non-canonical). Confirm `.env.example` carries **placeholders only**
    (`TWS_USERID=your_paper_username`, `IBKR_CLIENT_ID=` blank, `TRADING_MODE=paper`,
    `READ_ONLY_API=yes`) — no real value (`.env.example:17-33`).
11. **The .env-write paths.** Saxo rotates its refresh token and persists each rotation back to
    `.env` ([`auth/token_persist.py`](../packages/infra-saxo/src/algotrading/infra_saxo/auth/token_persist.py),
    [`auth/env_tokens.py`](../packages/infra-saxo/src/algotrading/infra_saxo/auth/env_tokens.py)).
    Confirm it writes a **secret** to disk — then check the file mode: `write_text` does **not**
    restrict permissions (no `chmod 0o600`; grep for `chmod`/`umask` is clean repo-wide). Flag a
    world-readable `.env` holding a live token as MEDIUM/HIGH depending on the deploy.
12. **Provenance/manifest carry no secrets.** Spot-check the manifest/provenance fields
    (`packages/core/.../manifest.py`) — `environment` and config hashes are recorded; confirm no
    credential, token, or `.env` value is ever folded into a `ProvenanceStamp`, a `config_hashes`
    bundle, or a stored manifest (C7 explicitly excludes `environment.yaml` from the hashes — confirm
    that exclusion is real, not just documented).

### 5 — Dependencies *(start now)*
13. **pycryptodome, not pyCrypto.** ADR 0031 §2 mandates **pycryptodome** for OAuth signing. It is in
    **no `pyproject.toml` today** (grep clean) — so the live finding is a *guard*: if any
    `pyproject.toml` or `uv.lock` entry ever names `pycrypto`/`pyCrypto` (the abandoned, CVE-ridden
    package), that is HIGH. When the OAuth module lands, confirm the dependency added is
    `pycryptodome`.
14. **Known-vuln scan.** Run a dependency vulnerability check (`uv run pip-audit` if available, else
    note it as a gap to add) over the resolved `uv.lock`; list any advisory hit with the package,
    version, advisory id, and the fixed version.

## Review surface

Read [TESTING.md](TESTING.md) for the seam→contract-test map. This task adds **no new tests** — it
**verifies** that the security-relevant invariants are *already* asserted, and files a finding where
they are not. Specific checks the report must answer:
- The IBKR read-only invariant is asserted (it is — `test_snapshot_is_read_only`); the ADR-0031
  history GET, when it lands, is covered by the same assertion.
- No `place_order`/`transmit`/order-reply call exists outside an explicitly-gated 3B path (today:
  none exists at all — the report records that baseline).
- `git ls-files` exposes no secret; `.env.example` has no real value; the root gate
  (`uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`) is green so the
  review runs against a settled tree.

## Done criteria

A severity-ranked findings report exists (CRITICAL/HIGH/MEDIUM/LOW), each finding carrying a
`file:line` and a concrete fix, covering all five sections — with the order-seam section (2) either
completed against landed 3A/3B code or explicitly marked "deferred: 3A/3B not yet built". The owner
can read it and decide whether the 3B live gate may open. The report names which findings **block**
live transmission (the gate) versus which are advisory. No HIGH/CRITICAL finding is left without an
owning task to fix it.

## Gotchas

- **Do not flip the dangerous switch to test it.** This is a read-only review; never send a live or
  even paper order to "verify" the gate — reason about the code path and assert on it.
- The order seam is the point of the task but **does not exist yet**; resist reviewing a phantom.
  Start on auth/secrets/BFF/deps now and re-open section 2 when 3A/3B land. State going in is the
  honest baseline: the safe default is currently "no order code at all."
- `verify_tls=False` is **correct** for the localhost self-signed Gateway — flag it only if a path
  combines TLS-off with an off-localhost base URL, not as a blanket finding.
- The BFF is single-operator **by design** (no multi-user auth) — that is a stated assumption, not a
  bug. The finding to hunt is anything that *exposes* a write or a secret under that assumption (an
  open bind address, a CORS `*`, a route that writes a credential), not the absence of login.
- The no-secrets rule is convention-only (no scan hook). Recommending a CI secret-scan is in scope as
  a finding; **adding** it is the [`ci-pipeline`](ci-pipeline.md) task's job, cross-ref it — don't
  build it here.
