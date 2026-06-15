# T-audit-2026-06-14-findings — strategic audit of the post-cleaning 24h burst

> **⚠️ BACKPORT NOTE (2026-06-15) — read this if you are merging `execution-booking-commit`.**
> These four P1 fixes were authored on the booking line (`execution-booking-commit` / `perso/main`,
> tip `fca222d`), which forked from `6fe6e7d`. The core-fleet integration that rebuilt the booking
> chain on `main` branched from that **same** `6fe6e7d` and re-did the *feature* (booking package +
> BFF + web, restructured) but **did not carry these four `fca222d` audit fixes forward** — they were
> silently dropped in the re-integration. This file + the code fixes were **re-applied on top of the
> current `main`** (branch `audit-p1-backport`) by re-implementing the *intent* against main's
> restructured files (not a cherry-pick — the files diverged). If you hit a conflict merging the stale
> `execution-booking-commit` into `main`, **the canonical version is here**: discard the stale branch's
> copy of these four fixes, they already live on `main`. Verified per-fix below; gate re-run green.

> **Source:** owner-requested strategic audit (2026-06-14) of the code landed since the
> 2026-06-13 doc-hygiene cleaning — features: §7.2 second-order Greeks, §5.4 scenario rate-axis,
> 3A order ticket (+ BFF + web), forward Eq-5 rate step 1, surface `min_points_per_slice`,
> the boundary contract/business tests, and the execution booking chain (fills store +
> password-gated commit). Altitude: strategic (big coquilles, not line-level). Gate was green
> throughout — none of these break the gate; they are correctness/provenance/cleanliness gaps.

**Verdict:** solid. Greeks formulas/units, gate fail-closed, append-only, determinism, two-gates
separation and the boundary tests were all verified correct. Below are the tracked follow-ups.

## P1 — fix  ✅ all four resolved 2026-06-14 (gate green: py 1813 + web 76)

> **Backport status on `main` (2026-06-15):** P1.1 ❌ was missing on `main` → **re-applied** (the
> persisted `ScenarioResult` lacked `rate_shock`; only the *config* side carried it). P1.2 ✅ already
> covered on `main` by `test_pricing.py`'s vanna/volga/charm central-difference oracles — the only
> `fca222d` extra was exercising them through the boundary *dispatch* path (belt-and-suspenders, not a
> correctness gap). P1.3 ❌ was missing → **re-applied** (the `TargetBroker`/`TimeInForce` enum-derived
> defaults + `GET /api/ticket/options` + the un-hardcoded `TicketPanel` selectors). P1.4 ✅ the
> `booking_id`↔decision test-pin already exists on `main`; only the fills-first **rationale comment**
> was missing → **re-applied** in `booking/commit.py`.

- [x] **`ScenarioResult` does not persist `rate_shock`** — FIXED: added
  `rate_shock: float | None = None` (additive-nullable) to the contract and populated it in
  `scenario_result()` from `cell.scenario.rate_shock`; golden row regenerated. A stored rate
  scenario is now distinguishable on replay.
- [x] **Boundary FD check omits the second-order Greeks** — FIXED:
  `test_analytic_greeks_match_finite_difference` now central-differences vanna/volga/charm through
  the `price()` dispatch path (vol bump for vanna/volga; T bump with DF tracking the rate for
  charm), so an engine-adapter sign/unit slip diverges at the boundary.
- [x] **Hardcoded UI selector lists + bare-literal BFF defaults** — FIXED: new
  `GET /api/ticket/options` derives brokers/TIFs from the `TargetBroker`/`TimeInForce` enums; the
  BFF model defaults from the enum values; `TicketPanel` fetches the options and populates its
  selectors from them (no `BROKERS`/`TIFS` literals). BFF + web tests added.
- [x] **Booking fill→audit write is non-atomic** — FIXED (documented + deliberate ordering):
  fills are written first by design (the book never claims a position the ledger lacks); the
  audit decision is appended last; the only crash window leaves durable fills with no decision —
  recoverable via each fill's `booking_id`. Rationale made explicit in `book()`; the
  `booking_id`↔decision link is test-pinned. True 2-phase atomicity left as future hardening.

## P1-followup — surfaced by the backport (2026-06-15), owner to rule

- [ ] **`ScenarioResult` also omits `correlation_shock`** — the same provenance gap P1.1 fixed for
  `rate_shock`, but for the correlation axis that landed *after* the audit (`infra-named-scenarios-
  and-corr-shock`). `Scenario` now carries `correlation_shock` but the persisted result row does
  not, so a stored ρ̄-bump cell reads identically to ρ̄+0.0 on replay. The clean fix mirrors P1.1
  (additive-nullable `correlation_shock: float | None = None` populated from
  `cell.scenario.correlation_shock`). **Not applied here** — it is outside the four audited P1s and
  the correlation axis is dormant on the live book; flagged for an owner ruling, not decided
  unilaterally. *(infra — contracts/scenarios)*

## P2 — cleanup pass

- [ ] `_BOOKING_CONFIG = sha256("execution-booking-commit/v1")` is an opaque inline tag —
  `execution/booking.py:~50`. Make it a named, greppable `_BOOKING_LOGIC_VERSION` constant like
  the other `*_VERSION` constants. *(execution)* — **NB:** `booking.py` is now the `booking/`
  package on `main`; re-locate the finding before acting.
- [ ] Paper `contract_key` format is a duplicated f-string and contradicts the `Fill` docstring's
  "names a concrete contract" invariant — `execution/booking.py:_leg_contract_key`. Share the
  separator/`OPT` convention with `infra/universe/contracts.py` and clarify the paper-mode
  exception in the `Fill` docstring. *(execution)*
- [ ] `_RESIDUAL_REL_FLOOR = 1e-4` is a tunable buried in code — `infra/forwards/estimate.py:~61`.
  Move it alongside the other forward heuristics in `ForwardConfig` / `pricing.yaml` `forward:`.
  *(forward/config lane — `core-explicit-rate-config`)*
- [ ] BFF rebuilds the JSONL fills ledger + audit log from disk on every request (O(n)) —
  `routers/booking.py:_booking_stores`. Cache on `AppContext` (or tail-read) before it is
  observable. *(execution / BFF)*

## Verified clean (no action)
Greeks closed-forms + signs + dollar units (Haug, FD-pinned); gate constant-time fail-closed;
append-only enforced on both ledgers (dup-id rejected, no delete verb); booking id determinism;
rate-axis units + forward-fixed rho; forward carry/rate split + labelled fallback; SVI routing
config-driven and floored; two-gates separation (asserted at import-graph level); `pricing.yaml`
/ `scenarios.yaml` clean/typed/versioned; boundary tests use genuine independent oracles with
budget+correctness paired.
