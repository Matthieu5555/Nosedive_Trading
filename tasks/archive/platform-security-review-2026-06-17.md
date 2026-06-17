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

---

## §2 — order seam (reviewed 2026-06-17, C6 landed)

When the archived refresh above was written, the 3B sign-and-send step did not
exist, so §2 was deferred. It has since landed (commit `5c63a61`, *3B sign-and-send
paper path — gated, fail-closed*). This section closes §2 against that landed seam.
It is a **read-only review corroborated by measurement** — no switch was flipped, no
env var set. Verified against the worktree off `main` at `37ab66c`: the security-
invariant suite is green (`uv run pytest` over `test_transmit_send.py`,
`test_transmit_decision.py`, `test_transmit_signing.py`, `test_transmit_gate.py`,
`test_transmit_audit.py`, `test_two_gates.py`, `test_booking_commit.py`,
`test_cp_rest_order_submit.py`, `test_cp_rest_adapter.py` → **105 passed**, exit 0).

### Headline

**No CRITICAL, no HIGH. Nothing in the landed seam blocks paper operation, and
nothing transmits to a live broker by default.** Every §2 invariant the spec named
holds with file:line evidence below. The 3B live gate may open **only** when the
owner sets it — and that owner action is, by construction, the single remaining gate
(M2 already closed). No finding here BLOCKS a future live flip; the items below are
the conditions on that flip, not defects against it.

### Transmission disabled by default — structurally blocked, submit never invoked

With `EXECUTION_TRANSMIT_ENABLED` absent, `load_transmit_gate` returns
`TransmitGate(mode=MODE_ABSENT, …)` (`transmit/gate.py:52-53`), and
`decide_transmission` short-circuits to `BLOCKED_DEFAULT` at
`transmit/decision.py:39-40` — *before* any binding, token, or sink check. The sink
is handed the blocked decision and `LiveSubmitSink.handle` returns early at
`transmit/live_sink.py:43-48` without ever calling `_submitter.submit`. The default
sink is `PaperSink` regardless (`transmit/send.py:84`), which never holds a submitter.
Pinned by `test_transmit_send.py::test_flag_absent_blocks_default_and_never_calls_the_submit_method`
(`:71-87`): a spy `OrderSubmitter` wired into a real `LiveSubmitSink`, gate loaded
from `{}`, asserts `decision is BLOCKED_DEFAULT` **and** `spy.calls == []`. **Measured
green.** This is the cited fail-closed-by-default proof.

### The owner gate is two-factor in spirit, checked at the SEND boundary, one source of truth

`decide_transmission` (`transmit/decision.py:30-55`) is one pure function gating the
send, and `transmit/send.py:101-117` calls it *before* `resolved_sink.handle`, so the
adjudication is the send boundary. A live send requires **all** of, in this order:
- the owner **config flag** `EXECUTION_TRANSMIT_ENABLED=live` (`gate.py:58`,
  `decision.py:52`);
- the **email sign-off token** — an HMAC-SHA256 over the canonical binding hash +
  approver + expiry (`signing.py:100-117`), verified constant-time with
  `hmac.compare_digest` against the `EXECUTION_SIGNOFF_SECRET`-keyed expectation
  (`signing.py:120-136`); checked at `decision.py:44-45`;
- the binding actually matches the exact 3A ticket (`binds_ticket`, `decision.py:42`)
  and is unexpired (`signoff_unexpired`, `decision.py:46-47`);
- **and** the single recorded-green handshake `EXECUTION_SECURITY_REVIEW=green`
  (`gate.py:66-67` sets `security_review_green`; `decision.py:53-54` returns
  `BLOCKED_GATE_OFF` unless it is true).

The flag-without-review path is `BLOCKED_GATE_OFF`, so the recorded-green handshake
is a hard, separate factor — **confirmed there is ONE source of truth for it**: the
`EXECUTION_SECURITY_REVIEW=green` env value, read once in `load_transmit_gate`
(`gate.py:8,66`) and consulted once in `decide_transmission`. This *is* the
"recorded-green security-review handshake" the 3B task references; no second source
was created. **Not bypassable by calling the seam directly**: the only path to a live
submit is `LiveSubmitSink.handle`, which itself re-checks `decision is SENT_LIVE`
(`live_sink.py:43`) before touching the submitter — a caller who constructs a
`LiveSubmitSink` and feeds it any non-`SENT_LIVE` decision gets a no-op. The decision
table is pinned end-to-end by `test_transmit_decision.py` (hand-written oracle) and
the three `test_transmit_send.py` cases (absent→blocked, paper→no-bytes, live+green→
exactly one submit call with the right binding hash).

### Audit-log completeness — write-ahead, append-only, reorder-stable

`transmit` records every transition to the audit log **before** the side effect:
`gate_evaluated` (seq 0) and `decision` (seq 1) are appended (`send.py:88-115`)
*before* `resolved_sink.handle(...)` (`send.py:117`), and `transmit_attempt` (seq 2)
captures the outcome. The log is append-only — the `TransmitAuditLog` Protocol
exposes only `append`/`read` (`audit.py:79-84`), both JSONL/in-memory impls open in
append mode and reject a duplicate `event_id` (`audit.py:67-72,97-99,120-126`), and
`replay` sorts by `(binding_hash, sequence)` so the trail is reorder-stable
(`audit.py:75-76`). `test_transmit_audit.py` covers append-only / reorder-stable /
cross-process hash stability; `test_transmit_send.py::test_transmit_writes_a_stamped_audit_trail`
(`:134-148`) asserts the exact event order `["gate_evaluated","decision","transmit_attempt"]`
with a non-empty stamp on each. On the **booking** side, M2's write-ahead discipline
holds: `commit.py:215` appends the audit *before* `ledger.append_many(fills)` at
`commit.py:216`, pinned by `test_the_commit_path_persists_the_audit_before_the_fills`.

### No credentials in the app; routes through the NEW separate IBKR leaf verb (ADR 0024 §4)

The live submit is a distinct class `CpRestOrderSubmit` with its own `submit` verb
(`infra-ibkr/.../connectivity/cp_rest_order_submit.py:24-41`), POSTing to
`/iserver/account/{account_id}/orders` over an injected `SupportsOrderPost` transport
— it holds **no credential**, only an account id. It is a *separate* method/class
from the read-only ingestion adapter, exactly as ADR 0024 §4 requires (read-only
invariant: "REST order endpoints never called"). The ingestion read-only invariant is
**still asserted and still green**:
- `test_cp_rest_order_submit.py::test_the_ingestion_adapter_still_never_touches_an_order_path`
  (`:43-55`) drives the real `CpRestMarketDataAdapter.snapshot()` and asserts no
  `order` substring appears in any GET or POST path (cites ADR 0024 §4);
- `…::test_order_submit_is_not_a_method_on_the_ingestion_adapter` (`:58-61`) and
  `…::test_submit_is_a_distinct_class_from_the_ingestion_adapter` (`:64-68`) pin the
  separation at the type level;
- the original `test_cp_rest_adapter.py::test_snapshot_is_read_only` (`:67-72`) still
  asserts `post_paths == []` and no `order` path on the ingestion surface.

The execution package does not re-export the live sink or the submitter: `transmit`
is **absent** from `packages/execution/__init__.py.__all__`, so reaching the live
submit requires an explicit `from algotrading.execution.transmit.live_sink import …`.
The seam is also **not wired into the BFF** — the `/api/ticket` and `/api/book`
routes carry `gated: {transmit: false}` and never import `transmit`
(grep-confirmed), so there is no HTTP path to a send at all today.

### Scrutiny of C6's narrowing of `test_two_gates.py` — tightened, not gutted

C6 (`5c63a61`) made exactly two changes to this guard (verified via `git show`):
1. it **removed the string `"transmit"` from `_FORBIDDEN`**, and
2. it **added** `test_the_live_submit_sink_is_not_reachable_from_the_transmit_package_surface`
   plus the `_LIVE_SINK_NAMES = ("LiveSubmitSink", "OrderSubmitter")` set.

The removal was *necessary and correct*, not a hole: the package now legitimately
contains a `transmit` submodule, so a blanket `"transmit" in name.lower()` ban would
have been a false positive that forces hiding the whole gated seam — it never
protected against a *live submit* path, only against the literal token. The
protection that matters was **tightened**: the credential/submit token ban
(`place_order`, `submit_order`, `send_order`, `credential`, `api_key`, `secret`,
`oauth`, `broker_client`) is **unchanged** and still runs over both the top package
(`test_…package_exports_no_unguarded_submit_or_credential_symbol`, `:30-36`) and
**every** submodule via `pkgutil.walk_packages` (`:39-47`) — so an unguarded
`submit_order`/`credential` symbol anywhere under `algotrading.execution` still fails
the build. The new test is **real and meaningful**: it imports the actual
`algotrading.execution.transmit` package and asserts neither `LiveSubmitSink` nor
`OrderSubmitter` is in its public surface — i.e. the only money-moving sink is
reachable solely by explicit deep import, never via the package façade. Measured: the
file's four tests pass. **No hole introduced** — the narrowing swapped a
coarse string match (that would now misfire) for a precise reachability assertion on
the exact dangerous symbols, while leaving the credential/submit ban fully intact.

### Depth-review note (the write barrier as one module)

A separate `review-module-depth` pass over the booking→fills→audit chain + the
transmit seam found the interface **deep and fail-closed**, with no defect worth a
code change. Two **advisory** observations, neither blocking: (a) `transmit()` takes
seven keyword dependencies including a `mint_event_id: Callable[[int], str]` the
caller must supply — depth is fine but a small `TransmitContext` would reduce the
orchestration surface for future callers; (b) the JSONL audit/ledger reload-on-init
silently truncates trailing blank lines (`audit.py:114`, `ledger.py:86`) which is
benign but means a partially-written final record is dropped rather than flagged.
Both are advisory; filed as observations, not defects.

### §2 verdict — does anything BLOCK a future live flip?

**No. Nothing in the landed seam blocks a future live flip, and the review found no
defect against it.** The seam is fail-closed by default and correctly gated. The live
flip is itself the gate: it opens only when the **owner** sets
`EXECUTION_TRANSMIT_ENABLED=live`, provisions `EXECUTION_SIGNOFF_SECRET` and a valid
per-ticket sign-off token, and records the green handshake
`EXECUTION_SECURITY_REVIEW=green`. With **this** recorded-green §2 review and M2
already closed, that owner action is the **single remaining gate** — there is no
hidden code prerequisite left. **The 3B live gate may open once the owner sets it.**
(Operationally, before a first live flip the owner should also pin a non-loopback
guard / TLS default per the still-advisory M3/L1 *if* a credentialed remote IBKR
endpoint is ever used; the local Gateway path that 3B's submit transport rides
carries no secret, so M3/L1 remain advisory, not blocking.)
