# platform-security-review — pre-live-order findings report (2026-06-17 refresh)

**Ran against:** worktree `security-m2-fix` branched off `main` at `985a40f`
(`docs(tasks): claim Stream-C rows`), tree clean at audit time.
**Sections:** 1 (IBKR auth), 3 (BFF), 4 (secrets/config), 5 (deps) — completed.
Section 2 (order seam): 3A (order ticket) and the paper booking chain are landed
and reviewed below; the **3B live-transmit / sign-and-send step has not landed**
(grep-confirmed clean), so its specific invariants stay deferred.

**Method:** read-only walk of the named files, corroborated by measurement.
Verified against the running tree at `985a40f`:
- `uv run ruff check .` → exit 0; `uv run mypy .` → exit 0; `uv run lint-imports`
  → exit 0; `uv run pytest` → **2451 passed, 12 skipped**, exit 0.
- `uv run pip-audit` → ran clean except the two `starlette` advisories below.
- The security-invariant tests are green: `test_snapshot_is_read_only`,
  `test_history_path_is_read_only`, `test_collector_is_read_only_only_portfolio_and_trades_gets_no_post`,
  `test_two_gates`, `test_booking_commit`, `test_booking_audit`, `test_concretization`.

> **This pass also CLOSED the one gating MEDIUM (M2).** Unlike the archived verdict
> (which was read-only), this run carried the M2 fix + a regression test that pins
> the commit-path write order. M2 is now **CLOSED** — see below. No other code was
> changed by this review; every other fix it names lands in its owning task.

---

## What changed since the archived verdict (`platform-security-review-report.md`)

The archived report was written against `23f4c75` and left M2 open as the standing
live-gate. This refresh runs against `985a40f` and differs in two ways:

1. **M2 is fixed and closed here** (the booking commit path now writes the audit
   write-ahead of the fills). Details under MEDIUM below.
2. All other findings re-measured against the current tree: M1 (starlette CVEs),
   M3/L1 (gateway TLS default + loopback guard), L2 (OAuth error repr) all still
   stand at the line numbers given. The §4 `.env.example` line numbers have shifted
   (placeholders unchanged); the credentialed-path TLS guarantee still holds.

---

## The headline

Measured, not assumed: the genuinely dangerous switch — a live (non-paper) order
transmit path — **still does not exist** anywhere under `packages/` or `apps/`. A
grep for `place_order|submit_order|send_order|reqPlaceOrder|.transmit|.reply(`
outside tests/labels returns nothing; `packages/execution/__init__.py` exports no
transmit/credential symbol. Fills are paper-only at construction. So the two HIGH
tripwires the spec told me to hunt both resolve downward when measured: there is no
off-localhost-with-TLS-off *credential* path (the secret-bearing path verifies TLS),
and there is no transmit-without-gate path (no transmit path at all).

**Verdict up front: no CRITICAL, no HIGH. Paper/read-only operation is not blocked
by anything here.** The one finding that gated 3B going live — M2, the audit
write-ahead discipline — is now **CLOSED**. Everything remaining is advisory.

---

## Findings (severity-ranked)

### MEDIUM

**M1 — `starlette 1.2.1` carries two fixable CVEs (BFF framework). Advisory — does not block 3B.**
`uv run pip-audit` reports `CVE-2026-54282` (fix `1.3.0`) and `CVE-2026-54283`
(fix `1.3.1`) in `starlette 1.2.1` (`uv.lock:3346-3352`), pulled transitively by
`fastapi>=0.136.3` (`apps/frontend/pyproject.toml:8`). The BFF is loopback-bound and
single-operator (see "What holds"), which caps real exposure, but a web framework
with known fixable CVEs should be patched. **Fix:** raise the `fastapi` floor (or add
a `starlette>=1.3.1` constraint) and re-resolve `uv.lock`. *Owning task:* the
`apps/frontend` dep bump / dep-scan lane.

**M2 — Booking wrote the fill ledger *before* the audit record (write-ahead violated). NOW CLOSED in this change.**
Before: `commit.py` ran `ledger.append_many(fills)` first, then built and appended
the `BookingAudit`. If the process died or `audit_log.append` raised (duplicate
`audit_id`, JSONL/IO error) in the window between, fills were durably committed with
**no audit record** — exactly the "a write with no prior audit record" the spec flags
(§2.6). The *block* path was always correctly audited-first (`commit.py:144-153`);
only the commit path inverted the order.
**Fix applied:** the commit path now builds the audit and calls
`audit_log.append(audit)` (`commit.py:215`) **before** `ledger.append_many(fills)`
(`commit.py:216`), mirroring the block path's audit-first discipline. The audit
content and the returned `BookingCommitted` are byte-for-byte unchanged — only the
write order moved. A regression test pins it:
`test_the_commit_path_persists_the_audit_before_the_fills` (in
`packages/execution/tests/test_booking_commit.py`) instruments both the ledger and
the audit log to record their append order on a shared tape and asserts
`tape == ["audit", "fills"]`; it was confirmed to **fail** on the old ordering
(`['fills', 'audit']`) and pass on the fixed ordering. *Owning task:* closed here;
[execution-order-sign-and-send](execution-order-sign-and-send.md) (3B) inherits this
now-correct commit path.

**M3 — `CpRestTransport.verify_tls` defaults to `False`. Advisory.**
`packages/infra-ibkr/src/algotrading/infra_ibkr/connectivity/cp_rest_transport.py:82`
— an unsafe default on a class whose `base_url` defaults to a real `https://` scheme
(`:15`, `:80`). It is **contained today**: both production builders pin it correctly —
the gateway builder uses localhost + carries no credential
(`session_factory.py:44-46`), and the credentialed builder verifies TLS
(`make_lst_http_post` defaults `verify_tls=True` at `cp_rest_credentials.py:89`;
`build_signed_cp_rest_transport` defaults `verify_tls=True` at `cp_rest_lst.py:213`).
The risk is a *future* direct `CpRestTransport(base_url=<remote>)` caller silently
disabling TLS verification. **Fix:** flip the default to `verify_tls=True` and have
the gateway path opt out explicitly for the self-signed localhost Gateway.
*Owning task:* the infra-ibkr auth package (pairs with
[ibkr-unattended-reauth](ibkr-unattended-reauth.md)).

### LOW

**L1 — `IBKR_CP_GATEWAY_URL` is not asserted to be loopback while `verify_tls=False` is hardcoded.**
`session_factory.py:44-46` — the gateway base URL is env-overridable
(`base_url = resolved.get(ENV_GATEWAY_URL, "") or _GATEWAY_DEFAULT_BASE_URL`) and the
transport is built `CpRestTransport(base_url=base_url, verify_tls=False)` with no guard
that the host stays localhost. The spec's tripwire ("off-localhost + TLS-off → HIGH")
technically matches, but the blast radius is **LOW, not HIGH**: this gateway transport
carries **no OAuth secret** (auth is the local Gateway's loopback cookie, meaningless
off-box). **Fix:** in the gateway builder, reject a non-loopback `IBKR_CP_GATEWAY_URL`
unless TLS verification is explicitly enabled. *Owning task:* infra-ibkr auth package.

**L2 — OAuth-exchange errors `repr()` the full IBKR response.**
`cp_rest_lst.py:158` and `:189` interpolate `{rt_response!r}` / `{lst_response!r}` into
`CpOAuthError` messages on a malformed-but-present response. Those responses can carry
`oauth_token` / `diffie_hellman_response` material, which would then surface into logs
or tracebacks. (The neighbouring field-name errors at `cp_rest_oauth.py:68` already do
the right thing — they quote only the missing field name.) **Fix:** log only the
missing-field name, not the response body. *Owning task:* infra-ibkr auth package.

**L3 — The BFF "store opens read-only for serving" posture has an unnamed write-exception (booking).**
`apps/frontend/.../routers/booking.py:56` does `booking_dir.mkdir(parents=True, ...)`
and the commit path writes `fills.jsonl` + `booking_audit.jsonl`
(`booking.py:33-34`, `commit.py:215-216`) under the store root. This is correct by
design (paper booking, loopback, password-gated) and *permitted* by the layer
contract — but the README's "the store opens read-only" claim does not name it. The
spec (§3.8) asks the write exceptions be **named**. **Fix:** name the booking
write-exception in `apps/frontend/README.md` (and/or relocate booking artifacts
outside the read-only serving root). *Owning task:* frontend BFF docs. Doc-accuracy /
posture, not a vuln.

### Adjudicated and dismissed (so they are not re-raised)

- **BFF imports `execution` / `strategy`** — **not** a layering violation. The
  import-linter contract is `… ← {strategy, execution} ← apps/frontend`; the BFF is
  the top layer and may read down. `lint-imports` is green.
- **`POST /api/run`** — the only runnable provider replays a committed day into a
  `TemporaryDirectory`; no transmit path, no write to the real store.
- **HMAC-SHA1 in `cp_rest_lst.py`** — IBKR-protocol-mandated (DH key-derivation and
  LST signature validation over a DH-derived secret), **not** request signing. Request
  signing is RSA-SHA256 / HMAC-SHA256. Not a weakness.
- **`PKCS1_v1_5` cipher in `cp_rest_lst.py`** — IBKR-mandated for the
  access-token-secret decrypt; protocol-fixed.

---

## What holds (the invariants — verified, with evidence)

**Section 1 — IBKR auth**
- Read-only invariant enforced **and tested on all three IBKR surfaces**: snapshot
  (`test_cp_rest_adapter.py:67`), the ADR-0031 history GET
  (`test_cp_rest_history.py:86`), and account-read (`test_cp_rest_account.py:149`).
  The spec's requirement — that the history GET be covered by the same assertion when
  it lands — is met. Green.
- OAuth 1.0a: signing via **pycryptodome** (`infra-ibkr/pyproject.toml:11`, resolved
  3.23.0). Consumer key / tokens / PEM read from `IBKR_CP_*` env; PEM env vars hold
  file *paths*, read with `Path(...).read_text()`, never inline key bytes.
- The **secret-bearing path verifies TLS**: `build_credentialed_session`
  (`session_factory.py`) → `make_lst_http_post` (`cp_rest_credentials.py:89`,
  `verify_tls=True`) and `build_signed_cp_rest_transport` (`cp_rest_lst.py:213`,
  `verify_tls=True`). The credentialed path to a remote IBKR endpoint is verified;
  only the no-secret localhost gateway path turns TLS off (M3/L1).

**Section 2 — order seam (the part that has landed)**
- **No live-transmit path exists** anywhere under `packages/`+`apps/` (grep clean,
  outside tests/labels). `packages/execution/__init__.py` exports no
  transmit/credential symbol. Green.
- Fills are paper-only at construction; a live fill is unconstructable.
- The BFF commit resolver is hard-stubbed fail-closed
  (`routers/booking.py:39-51` `_PendingConcretizationResolver` always raises), so even
  a correct password yields `unresolvable_leg` with no fill written end-to-end.
- Password gate (`booking/password_gate.py`): scrypt + constant-time
  `secrets.compare_digest`, salt + digest from env, fail-closed on
  unconfigured/malformed/absent/wrong.
- Audit log (`booking/audit.py`): append-only (Protocol exposes only `append`/`read`;
  JSONL opens append-mode), carries who/what/when + decision, rejects duplicate
  `audit_id` and forged provenance stamps; `test_booking_audit.py` asserts no
  delete/remove/update/pop/clear/mutate verb exists. With **M2 closed**, the commit
  path now persists this audit *before* the fill write.

**Section 3 — BFF**
- CORS is env-driven (`app.py:37-42`): `allow_origins=[FRONTEND_BASE_URL]` (default
  `http://localhost:5173`), **not** `*`; `allow_methods=["GET","POST","DELETE"]`;
  `allow_credentials` is unset → `False`; `allow_headers=["*"]` is acceptable given no
  credentialed/cookie auth.
- Host bind is **loopback**: `__main__.py:15` `host="127.0.0.1"`; no `0.0.0.0`. The
  no-multi-user-auth single-operator design is acceptable **as long as this loopback
  bind is never changed** — the standing invariant.
- Every non-GET route adjudicated: pure compute / read, sample-only run, or the
  password-gated paper booking write (L3). **No `/api/oauth` route exists**
  (grep-clean) — the deleted Saxo router has not reappeared. No route writes a secret
  or reaches a transmit path.

**Section 4 — secrets & config**
- `.gitignore:28-30` ignores `.env` + `.env.*` with `!.env.example`; `git ls-files`
  exposes only `.env.example`, which carries **placeholders only** (`IBKR_CP_*` blank
  at `.env.example:32-39`, `TWS_USERID=your_ibkr_username` at `:83`,
  `TRADING_MODE=paper` at `:86`, `READ_ONLY_API=yes` at `:88`) — no real value.
- No runtime credential/`.env`/PEM/cookie writer exists (repo-wide sweep) — the app
  only *reads* operator-provisioned PEM files, so the chmod-0o600 concern is N/A
  (nothing world-readable is written by the app).
- Manifest/provenance fold **no** secret: `environment` is structurally excluded from
  `config_hashes`/`config_snapshot` (they iterate only `SECTION_NAMES`), and
  `PlatformConfig` has `extra="forbid"` with no `environment` section. C7's exclusion
  is enforced in code, not just documented.

**Section 5 — dependencies**
- **pycryptodome, not pyCrypto** (`infra-ibkr/pyproject.toml:11`); no `pycrypto` /
  `pyCrypto` string anywhere in `pyproject.toml` / `uv.lock` (only `pycryptodome` at
  `uv.lock:163,178,2654+`). The forward-looking guard is satisfied.
- Dependency-vuln **and** secret scanning are already wired (CI `.github/workflows/scan.yml`
  + pre-commit `detect-secrets` + `.secrets.baseline`). The spec's "recommend a CI
  secret-scan" item is **already closed** — the only live dep finding is M1.

---

## The gate — what blocks 3B going live

- **No CRITICAL/HIGH finding. Paper/read-only operation is not blocked by anything here.**
- **The one finding that gated 3B (M2) is now CLOSED** in this change: the booking
  audit is written write-ahead of the fills, pinned by a regression test. 3B inherits
  this corrected commit path.
- **Section 2 stays partly open by design.** 3A and the paper booking chain are
  reviewed and clean above; the **3B live-transmit / sign-and-send step does not exist
  yet** (transmit grep clean), so its specific invariants — the two-factor owner gate
  (config flag **and** email confirmation) checked *at the send boundary* and
  unbypassable by calling the seam directly — **cannot be reviewed until it lands**.
  When [execution-order-sign-and-send](execution-order-sign-and-send.md) lands, re-open
  this report's §2 against it. That review (a fresh `security-review` pass recorded
  green against the landed 3B seam) is the green light — with M2 already closed, it is
  the only remaining gate. This is the single "recorded-green security-review"
  handshake the 3B task references; do not create a second source of truth.
- M1 / M3 / L1 / L2 / L3 are **advisory** — none blocks live transmission. Patch the
  BFF framework, harden the TLS default and the gateway-URL guard, redact the OAuth
  error repr, and name the booking write-exception in their owning tasks.

**Every finding has an owning task; none is left orphaned.** M2 → closed here; M1 →
`apps/frontend` dep bump / dep-scan lane; M3/L1/L2 → the infra-ibkr auth package
(pairs with `ibkr-unattended-reauth`); L3 → the frontend BFF README.
