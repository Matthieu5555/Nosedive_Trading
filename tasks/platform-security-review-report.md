# platform-security-review — pre-live-order findings report

**Ran against:** worktree `security-review` branched off `main` at `23f4c75`
(`refactor(market): simplify the constituents panel`), tree clean at audit time.
**Sections:** 1 (IBKR auth), 3 (BFF), 4 (secrets/config), 5 (deps) — completed.
Section 2 (order seam) is **partially reviewable now** and reviewed below: 3A
(order ticket) and the paper booking chain have landed since the spec baseline;
the 3B live-transmit step has **not** — that half stays deferred.

**Method:** read-only walk of the named files, corroborated by measurement, not by
reading labels. Confirmed against the running tree:
- `uv run ruff check .` → exit 0; `uv run lint-imports` → exit 0.
- 114 security-invariant tests green (`test_cp_rest_adapter` / `_history` /
  `_account` read-only asserts, `test_two_gates`, `test_booking_commit` /
  `_audit`, `test_concretization`, `test_order_ticket`, `test_ticket_api`,
  `test_booking_api`).
- `pip-audit` over the resolved environment (dependency CVE scan).
- Full `mypy` + full `pytest` not re-run this pass; the board records the gate
  green 2026-06-14 and the three checks above ran clean on `23f4c75`.

> **No code was modified.** This is a verdict. Each fix it names lands in an owning
> task, cross-referenced per finding.

---

## The headline: the spec baseline (2026-06-07) has moved — measured, not assumed

The task's "State going in" describes a tree where the dangerous path does not exist
*at all*. Four things have landed since, and the report is written against the tree,
not the spec:

| Spec baseline said | Measured on `23f4c75` |
|--------------------|------------------------|
| `packages/execution` is one docstring line; no order code anywhere | The **3A order ticket** (`infra/orders/ticket.py`) and the **paper booking chain** (`packages/execution/*`: concretize → password-gate → book → fills ledger → audit) have **landed**, paper-only |
| OAuth 1.0a signing module (ADR 0031) does not exist | It **landed**: `connectivity/cp_rest_lst.py` + `cp_rest_oauth.py` |
| pycryptodome in no `pyproject.toml` | **Present**: `packages/infra-ibkr/pyproject.toml:11` (`pycryptodome>=3.20`, resolved 3.23.0) |
| No automated secret-scan; no-secrets rule is convention-only | **Wired**: `.pre-commit-config.yaml` (detect-secrets), `.github/workflows/scan.yml` (detect-secrets + pip-audit jobs), `.secrets.baseline` |
| — | The **3B live-transmit step still does not exist** — the genuinely dangerous switch is absent (grep-confirmed below) |

So the two HIGH tripwires the spec told me to hunt **both resolve downward** once
measured: there is no off-localhost-with-TLS-off *credential* path (the secret-bearing
path verifies TLS), and there is no transmit-without-gate path (no transmit path at all).

**Verdict up front: no CRITICAL, no HIGH.** Nothing here blocks paper/read-only
operation. One finding (M2) is the audit-completeness discipline the order seam needs
closed *before* 3B flips live; the rest are advisory.

---

## Findings (severity-ranked)

### MEDIUM

**M1 — `starlette 1.2.1` carries two fixable CVEs (BFF framework).**
`pip-audit` reports `CVE-2026-54282` (fix `1.3.0`) and `CVE-2026-54283` (fix `1.3.1`)
in `starlette 1.2.1` (`uv.lock:3346-3348`), pulled transitively by
`fastapi>=0.136.3` (`apps/frontend/pyproject.toml:8`). The BFF is loopback-bound and
single-operator (see PASS list), which caps real exposure, but a web framework with
known fixable CVEs should be patched. **Fix:** raise the `fastapi` floor (or add a
`starlette>=1.3.1` constraint) and re-resolve `uv.lock`; confirm the `scan.yml`
`dep-vuln` job (which uses `-s osv`) actually flags it — my run used the default
source, and OSV ingestion may lag, so the CI gate could be silently green on this.
*Owning task:* the dependency bump lands against `apps/frontend` / the dep-scan lane.
**Advisory — does not block 3B.**

**M2 — Booking writes the fill ledger *before* the audit record (write-ahead violated).**
`packages/execution/src/algotrading/execution/booking/commit.py:206-216` runs
`ledger.append_many(fills)` first, then builds and appends the `BookingAudit`. If the
process dies or `audit_log.append` raises (duplicate `audit_id`, or a JSONL disk/IO
error) in the window between, fills are durably committed with **no audit record** —
exactly the "a write with no prior audit record" the spec flags (§2.6). The *block*
path is correctly audited-before-return (`commit.py:144-154`); only the commit path
inverts the order. Paper-only and single-file today, hence MEDIUM not HIGH — but the
audit is the write-ahead record and must precede the fill write. **Fix:** append the
audit record before `ledger.append_many`, or wrap both so a post-ledger audit failure
re-raises loudly / rolls back. *Owning task:*
[execution-order-sign-and-send](execution-order-sign-and-send.md) (3B) — **this is
the one finding the owner should close before flipping 3B live**, since 3B inherits
this same commit path.

**M3 — `CpRestTransport.verify_tls` defaults to `False`.**
`packages/infra-ibkr/.../connectivity/cp_rest_transport.py:82` — an unsafe default on a
class whose `base_url` defaults to a real `https://` scheme. It is **contained today**:
both production builders pin it correctly — the gateway builder uses localhost + carries
no credential, and the credentialed builder verifies TLS (see PASS list). The risk is a
*future* direct `CpRestTransport(base_url=<remote>)` caller silently disabling TLS
verification. **Fix:** flip the default to `verify_tls=True` and have
`build_gateway_session` opt out explicitly for the self-signed localhost Gateway.
*Owning task:* the infra-ibkr auth package (pairs with
[ibkr-unattended-reauth](ibkr-unattended-reauth.md)). **Advisory.**

### LOW

**L1 — `IBKR_CP_GATEWAY_URL` is not asserted to be loopback while `verify_tls=False` is hardcoded.**
`session_factory.py:44-46` — the gateway base URL is env-overridable and the transport
is built `verify_tls=False` with no guard that the host stays localhost. The spec's
tripwire ("off-localhost + TLS-off → HIGH") technically matches, but the blast radius is
**LOW, not HIGH**, because this transport carries **no OAuth secret** (no `oauth_signer`;
auth is the local Gateway's loopback cookie, meaningless off-box). **Fix:** in
`build_gateway_session`, reject a non-loopback `IBKR_CP_GATEWAY_URL` unless TLS
verification is explicitly enabled. *Owning task:* infra-ibkr auth package.

**L2 — OAuth-exchange errors `repr()` the full IBKR response.**
`cp_rest_lst.py:158` and `:189` interpolate `{rt_response!r}` / `{lst_response!r}` into
`CpOAuthError` messages on a malformed-but-present response. Those responses can carry
`oauth_token` / `diffie_hellman_response` material, which would then surface into logs or
tracebacks. **Fix:** log only the missing-field name, not the response body. *Owning
task:* infra-ibkr auth package.

**L3 — The BFF "store opens read-only for serving" posture has an unnamed write-exception (booking).**
`apps/frontend/.../routers/booking.py:54-57` does `(<store_root>/booking).mkdir(...)`
and writes `fills.jsonl` + `booking_audit.jsonl` under the store root
(`commit.py:206-216`). This is correct by design (paper booking, loopback, password-gated)
and is *permitted* by the layer contract — but the README's "the store opens read-only —
only the EOD cron writes" claim does not name it. The spec (§3.8) asks that the write
exceptions be **named**. **Fix:** name the booking write-exception in
`apps/frontend/README.md` (and/or relocate booking artifacts outside the read-only
serving root). *Owning task:* frontend BFF docs. **Doc-accuracy / posture, not a vuln.**

### Adjudicated and dismissed (so they are not re-raised)

- **BFF imports `execution` / `strategy`** (`routers/booking.py`, `routers/backtest.py`)
  — **not** a layering violation. The import-linter contract is
  `… ← {strategy, execution} ← apps/frontend`; the BFF is the *top* layer and may read
  down into both. `lint-imports` is green. This is how `POST /api/booking/commit` and
  `POST /api/backtest/run` are meant to work.
- **`POST /api/run`** (`routers/run.py`) — confirmed safe: the only runnable provider is
  `SAMPLE`, which replays a committed day into a `TemporaryDirectory` store with
  `persist=False`; no transmit path, no write to the real store.
- **HMAC-SHA1 at `cp_rest_lst.py:120,131`** — IBKR-protocol-mandated (DH key-derivation
  and LST signature validation, over a DH-derived secret), **not** request signing.
  Request signing is HMAC-SHA256 / RSA-SHA256. Not a weakness.
- **`PKCS1_v1_5` cipher** (`cp_rest_lst.py:90`) — IBKR-mandated for the access-token-secret
  decrypt; the code uses the random-sentinel Bleichenbacher mitigation. Protocol-fixed.

---

## What holds (the invariants — verified, with evidence)

**Section 1 — IBKR auth**
- Read-only invariant enforced **and tested on all three IBKR surfaces**: snapshot
  (`test_cp_rest_adapter.py:67` `test_snapshot_is_read_only`), the ADR-0031 history GET
  (`test_cp_rest_history.py:86` `test_history_path_is_read_only`), and account-read
  (`test_cp_rest_account.py:149`). Each asserts `post_paths == []` and no `order`
  substring in any path. The spec's requirement — that the history GET be covered by the
  same assertion when it lands — is **met**. Green.
- OAuth 1.0a: pycryptodome (`cp_rest_lst.py:12-16`), RSA-SHA256 + HMAC-SHA256 request
  signing, CSPRNG nonces (`secrets.token_hex/token_bytes`), `hmac.compare_digest` for LST
  validation (`cp_rest_lst.py:132`). Consumer key / tokens / PEM read from `IBKR_CP_*`
  env; PEM env vars hold **file paths**, `Path(...).read_text()`
  (`cp_rest_credentials.py:44-55`), never inline key bytes. No secret is logged anywhere
  in the package; `CpOAuthError` messages quote env-var *names* and paths only (except L2).
- The **secret-bearing off-localhost path verifies TLS**: `build_credentialed_session`
  (`session_factory.py:56-77`) → `make_lst_http_post` (`cp_rest_credentials.py:89`,
  `verify_tls=True`) and `build_signed_cp_rest_transport` (`cp_rest_lst.py:213`,
  `verify_tls=True`). The credentialed path to `api.ibkr.com` is verified.

**Section 2 — order seam (the part that has landed)**
- **No live-transmit path exists.** Grep over `packages/`+`apps/` for
  `place_order|submit_order|send_order|reqPlaceOrder|\.transmit\b|\.reply\(` returns only
  tests, docs, UI labels, and the server-side `"transmit": False` flag
  (`serializers.py:526`). `packages/execution/__init__.py` `__all__` exports no
  transmit/credential symbol; `test_two_gates.py` walks **every** submodule
  (`pkgutil.walk_packages`) asserting the forbidden set
  (`transmit/place_order/submit_order/send_order/credential/api_key/secret/oauth/broker_client`)
  is empty. Green.
- Fill/ticket are **paper-only at construction** (`fills.py:57-58`, `ticket.py:117-118`):
  a live fill is unconstructable.
- The `"transmit": False` / 3B-gated flag is **server-side** (`serializers.py:525-528`),
  asserted by `test_ticket_api.py:96-103`. The BFF commit resolver is hard-stubbed
  fail-closed (`routers/booking.py:39-51` `_PendingConcretizationResolver` always raises),
  so even a correct password yields `unresolvable_leg` with no fill written end-to-end.
- Password gate (`booking/password_gate.py`): scrypt (N=2¹⁴, r=8, p=1), constant-time
  `secrets.compare_digest` (`:77`), salt + digest from env (`:9-10,:59-60`), fail-closed
  on unconfigured / malformed / absent / wrong.
- Audit log (`booking/audit.py`): append-only (Protocol exposes only `append`/`read`;
  JSONL opens append-mode `:135`), carries who/what/when + gate state, rejects duplicate
  `audit_id` (`:81-86`) and forged provenance stamps (`:80`); `test_booking_audit.py`
  asserts no `delete/remove/update/pop/clear/mutate` verb exists. (M2 is the *ordering*
  of the commit-path write, not the log's integrity.)

**Section 3 — BFF**
- CORS is env-driven (`app.py:37-43`): `allow_origins=[FRONTEND_BASE_URL]` (default
  `http://localhost:5173`), **not** `*`; `allow_methods=["GET","POST","DELETE"]`;
  `allow_credentials` unset → `False`. `allow_headers=["*"]` is acceptable given no
  credentialed/cookie auth.
- Host bind is **loopback everywhere**: `__main__.py` `host="127.0.0.1"`; Vite dev/preview
  and `/api` proxy at `127.0.0.1`; no `0.0.0.0`. The no-multi-user-auth design is
  acceptable **as long as this loopback bind is never changed** — the standing invariant.
- Every non-GET route adjudicated: `ticket/preview`, `basket/risk`, `basket/scenarios`,
  `price_history/batch` are pure compute / read; `run` is sample-only (above); `backtest`
  reads the store; `booking/commit` is the password-gated paper write (L3). **No
  `/api/oauth` route exists** (grep-clean) — the deleted Saxo router has not reappeared.
  No route writes a secret or reaches a transmit path.

**Section 4 — secrets & config**
- `.gitignore:28-30` ignores `.env` + `.env.*` with `!.env.example`; `git ls-files`
  exposes only `.env.example`, which carries **placeholders only** (`IBKR_CP_*` blank,
  `TWS_USERID=your_ibkr_username`, `TRADING_MODE=paper`, `READ_ONLY_API=yes`).
- No runtime credential/`.env`/PEM/cookie writer exists (repo-wide sweep) — the app only
  *reads* operator-provisioned PEM files, so the chmod-0o600 concern is N/A (nothing
  world-readable is written by the app). `load_env_file` (`core/paths.py`) does not log
  values and uses `interpolate=False`.
- Manifest/provenance fold **no** secret: `ProvenanceStamp` / `_canonical_stamp_hash`
  (`core/provenance.py`) and `Manifest.to_dict` (`core/manifest.py:31-43`) carry only
  calc ts / code version / config hashes / source keys / run ids / status. `environment`
  is **structurally excluded** from `config_hashes` and `config_snapshot` — they iterate
  only `SECTION_NAMES` (universe/qc/solver/surface/forward/scenario/monetization;
  `platform_config.py:465-473`), and `PlatformConfig` has no `environment` section and
  `extra="forbid"`. C7's exclusion is enforced *in code*, not just documented. The
  recorded `environment` value is a label (`os.environ.get("ALGOTRADING_ENV","production")`).

**Section 5 — dependencies**
- pycryptodome, not pyCrypto (`infra-ibkr/pyproject.toml:11`); no `pycrypto`/`pyCrypto`
  string anywhere in `pyproject.toml` / `uv.lock`. The forward-looking guard is satisfied.
- Dependency-vuln **and** secret scanning are already wired (CI `scan.yml` + pre-commit
  `detect-secrets` + `.secrets.baseline`; `pip-audit`/`detect-secrets` dev deps). The
  spec's "recommend a CI secret-scan" item is **already closed** — the only live dep
  finding is M1 (the starlette bump itself).

---

## The gate — what blocks 3B going live

- **No CRITICAL/HIGH finding. Paper/read-only operation is not blocked by anything here.**
- **Before the 3B live-order flag may flip:** close **M2** (audit must be write-ahead of
  the fill write — 3B inherits this commit path). That is the one finding that touches the
  audit-completeness discipline the spec gates live transmission on.
- **Section 2 stays partly open by design.** 3A and the paper booking chain are reviewed
  and clean above; the **3B live-transmit step does not exist yet**, so its specific
  invariants — the two-factor owner gate (config flag **and** email confirmation) checked
  *at the send boundary* and unbypassable by calling the seam directly — **cannot be
  reviewed until it lands**. Re-open this report's §2 when
  [execution-order-sign-and-send](execution-order-sign-and-send.md) lands; that review,
  plus M2 closed, is the green light.
- M1 / M3 / L1 / L2 / L3 are advisory: patch the BFF framework, harden the TLS default and
  the gateway-URL guard, redact the OAuth error repr, and name the booking write-exception.
  None gate live transmission.

**Every finding has an owning task; none is left orphaned.** M2 →
`execution-order-sign-and-send`; M1 → the `apps/frontend` dep bump / dep-scan lane;
M3/L1/L2 → the infra-ibkr auth package (pairs with `ibkr-unattended-reauth`); L3 →
the frontend BFF README.
