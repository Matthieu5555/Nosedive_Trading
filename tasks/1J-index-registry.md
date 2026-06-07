# 1J — Index registry: which indices to fetch + per-index exchange calendar

> **Phase 1, foundational (sits with P0/D1).** The capture pipeline (1A/1C/1G) assumes it knows
> *which indices to fetch* and *when each one closes* — but nothing supplies that today.
> `configs/universe.yaml` is a flat demo stub (`underlyings: [AAPL, MSFT, SPY]`), and the fetch time
> was an open decision (a single "US market hours" guess). This WS lands the **index registry**: a
> hashed `indices:` block in `universe.yaml`, a typed config object over it, and a calendar resolver
> that derives each index's session close from an exchange-calendar code. Per
> **[ADR 0035](../.agent/decisions/0035-index-registry-and-per-index-capture-schedule.md)**;
> **[ADR 0028](../.agent/decisions/0028-configuration-and-reproducibility-standard.md)** governs the
> hashed/typed-config discipline; **[ADR 0011](../.agent/decisions/0011-blueprint-as-plan-of-record.md)**
> overrides on every domain question.

- **Owns:** the `indices:` block in `configs/universe.yaml`; a typed `IndexRegistry` config object
  beside the existing universe config (`packages/infra/src/algotrading/infra/config/` — the C7
  bundle-loader path, mirror `UniverseConfig`/`from_config`); and a **calendar resolver** in
  `packages/infra/src/algotrading/infra/universe/` that maps an index's `calendar` code to its
  trading sessions / session close via the **`exchange_calendars`** library (new `uv` dependency).
- **Depends on:** C7 config loader (landed — `config_hashes["universe"]`, `from_config`, validation
  discipline). Nothing else; this is a pre-req, not a dependant.
- **Blocks:** **1A** (resolves membership for the registry's enabled indices), **1C** (capture
  iterates the enabled indices; close instant per the index calendar), **1G** (the timer schedule is
  derived per-index from the calendar). Also feeds **1I** (the index picker is the registry's enabled
  set).
- **State going in (audited 2026-06-07):** `configs/universe.yaml` holds `version`, `underlyings:
  [AAPL, MSFT, SPY]`, `exchange: SMART` — no indices, no per-instrument IBKR ref, no schedule. The C7
  typed-config + per-bundle-hash machinery exists and is tested (universe is already a hashed bundle).
  **No exchange-calendar library** is referenced anywhere in the repo (verified — no
  `exchange_calendars` / `pandas_market_calendars`). The run window is an *open decision* in
  `documentation/connectivity/server-deployment-plan.md` §4.3, not a design.

## Objective

A single hashed registry says **which indices the platform tracks**, each with its symbol, display
name, trading-calendar code, currency, IBKR contract reference, and an `enabled` switch. A typed
`IndexRegistry` loads and validates it (no economic value as a `.py` literal — the C7 no-hardcode
rule). A calendar resolver answers, for any enabled index and date, **"is this a session, and when
does it close?"** from the `exchange_calendars` library — so 1C captures the right close instant and
1G fires at the right time, per exchange, with holidays/half-days/DST handled by the library, never by
hand. Adding an index is then a one-entry edit; the cron picks it up on its next run.

## What to do (ordered)

1. **Add the `indices:` block to `configs/universe.yaml`** in the ADR 0035 shape: a keyed map of
   index symbol → `{ name, calendar, currency, ibkr: { conid, secType, exchange }, enabled }`. Land
   **SX5E** (calendar `XEUR`, EUREX) and **SPX** (calendar `XNYS`, CBOE) as the two seed entries.
   Real IBKR conids are verified against IBKR here (the `0` placeholders in the ADR are replaced);
   if a conid cannot be confirmed, land the entry **`enabled: false`** rather than guessing one.
   Equity capture at scale is gated on D1's `provider` segment (ADR 0034 §4), so seeding an index
   `enabled: false` until its capture path is ready is legitimate.
2. **Build the typed `IndexRegistry` config object** on the C7 loader path (mirror `UniverseConfig` /
   `from_config`): parse the block into a frozen typed structure with per-entry validation — non-empty
   symbol; `calendar` is a code the library actually knows (validate against
   `exchange_calendars.get_calendar_names()`, fail with a labeled error on an unknown code, **never**
   silently fall back to a default calendar); `currency` a 3-letter code; `ibkr.secType`/`exchange`
   non-empty; `enabled` a bool. It stays inside the hashed `universe` bundle —
   `config_hashes["universe"]` already covers it (ADR 0028); confirm no separate hash is introduced.
3. **Build the calendar resolver** in `infra/universe/`: given an index's `calendar` code, return its
   `exchange_calendars` calendar, and expose the two answers the pipeline needs — `is_session(index,
   date)` and `session_close(index, date)` (a **timezone-aware** instant). Wrap the library behind a
   thin port-style function so the rest of the code depends on our signature, not on
   `exchange_calendars` directly (swap-ability + one place to test). Inject any "today" — **never read
   a wall clock** inside the resolver (the byte-identical-replay discipline 1C/1G depend on).
4. **Expose the enabled-index set** through the universe service (`infra/universe/`) so 1A/1C/1G/1I all
   read one accessor (e.g. `enabled_indices()` → the registry entries) rather than re-parsing YAML.
   This is the single seam the downstream tasks consume.
5. **Add the `exchange_calendars` dependency via `uv`** (`uv add exchange_calendars`); record it as
   the deliberate dep ADR 0035 calls out (it belongs to this layer, not the 1G timer).
6. **Update the docs next to the code** (AGENTS.md "keep the docs alive"): `infra/universe/README.md`
   and `infra/config/README.md` gain the registry + calendar-resolver note; the connectivity run-window
   note is flipped by 1G (coordinated there).

## Test surface

Read [TESTING.md](TESTING.md). Expected values derived independently of the code under test (calendar
facts hand-encoded from known exchange holidays, not read back from the resolver). Specific:

- **Registry round-trip + validation.** A well-formed `indices:` block parses to the expected typed
  entries (symbols, calendar codes, `enabled` flags asserted against a hand-written fixture). At least
  one **malformed** entry is **rejected with a labeled error**, not coerced: an **unknown calendar
  code** (the load-bearing negative — assert it does *not* silently default to some calendar), an
  empty symbol, a bad currency, a non-bool `enabled`.
- **Enabled filter.** A registry mixing `enabled: true`/`false` exposes **only** the enabled set
  through the universe accessor; a disabled index is absent from `enabled_indices()` and never reaches
  capture.
- **Calendar resolver — session vs holiday (independent oracle).** For a **known exchange holiday**
  (e.g. a date Eurex/`XEUR` is closed and a date NYSE/`XNYS` is closed — different dates, hand-picked
  from the published calendars and named in the test), `is_session` returns **False**; for a known
  normal trading day, **True**. The two indices resolve to **different** session sets (proves it is
  per-index, not one global calendar).
- **Session close is timezone-correct.** `session_close(SX5E, d)` and `session_close(SPX, d)` for the
  same date return instants in the **right tz** and at **different UTC times** (Eurex close ≠ NYSE
  close) — asserted against hand-computed UTC instants, the exact look-ahead-sensitive value 1C/1G
  consume. A **half-day early close** (a known shortened session) resolves to the early close, not the
  regular one.
- **No wall-clock read.** The resolver takes an injected date/clock; a test proves no hidden `now()`
  (same discipline as `test_eod_run_*`).
- **Edge cases (TESTING.md floor):** an empty `indices:` block (valid → empty enabled set, not a
  crash); an index enabled with a calendar code the library lacks (rejected at load, per the negative
  above); a date before/after a calendar's coverage window (labeled, not a silent wrong answer).
- **Gate green:** `uv run ruff check . && uv run mypy . && uv run lint-imports && uv run pytest -q`.
  uv only.

## Done criteria

`configs/universe.yaml` carries the `indices:` block (SX5E + SPX seeded; conids verified or the entry
left `enabled: false`); a typed `IndexRegistry` loads + validates it inside the hashed `universe`
bundle (unknown calendar code rejected, never defaulted); the calendar resolver answers
`is_session`/`session_close` per index from `exchange_calendars` with correct tz/holiday/half-day
handling and no wall-clock read; the universe service exposes the enabled-index set as the single seam
for 1A/1C/1G/1I; `exchange_calendars` added via uv; READMEs updated; every case above has a named test
with an independent oracle; root gate green.

## Gotchas

- **The registry is *which indices*, not *what is in them*.** Membership (1A `IndexConstituent`) is a
  separate, bitemporal, look-ahead-gated concern. Do not fold constituents into the registry, and do
  not fold the registry into membership — ADR 0035 §3.
- **Never silently default an unknown calendar code.** A typo (`XEURX`) that falls back to some
  calendar captures the wrong close instant — a look-ahead bug. Validate against the library's known
  names at load and fail loudly.
- **The fetch time is derived, not stored.** Do not write literal close times into YAML; the hashed
  thing is the `calendar` *code*, the times come from the library at run time (ADR 0035 §4). Storing
  resolved times both rots and churns the config hash.
- **`enabled: false` is the safe default for an index whose capture isn't ready** (no verified conid,
  or D1's `provider` segment not yet landed for equity scale — ADR 0034 §4). List it disabled rather
  than guess a conid or enable a path that writes mixed-provider data.
- **Provider-agnostic core, IBKR sub-block.** Symbol/name/calendar/currency describe the index and
  stay provider-neutral; only `ibkr:` is IBKR-specific (a future Saxo/Deribit sibling sub-block joins
  under the same key). Keep that boundary so ADR 0023's multi-provider stance holds.
- **No wall clock anywhere on the resolve path** — inject the date. 1C's byte-identical replay and
  1G's idempotent ledger both break if the close instant depends on when the code ran.
- **uv only** — `uv add exchange_calendars`; no bare pip.
