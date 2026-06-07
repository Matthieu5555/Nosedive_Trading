# 0035 — Index registry + per-index capture schedule (exchange-calendar driven)

- **Status:** accepted, 2026-06-07 (owner-ruled the two forks in design review).
- **Date:** 2026-06-07.
- **Implements:** blueprint **Part VII** (configuration) — this refines, and stays inside,
  `universe.yaml`'s charter ("monitored underlyings, exchanges, product families… and cadence").
  No blueprint amendment is needed: the faithful transcription already routes the monitored set and
  capture cadence to `universe.yaml`; this ADR fixes the *shape*.
- **Relates to:** [[0028-configuration-and-reproducibility-standard]] (hashed vs operational config,
  per-bundle `config_hashes`), [[0031-ibkr-historical-data-cp-rest-oauth1a]] (the IBKR contract the
  registry points at), [[0032-unattended-scheduling-via-systemd-timers]] (the timer this schedules),
  [[0034-data-retention-compaction-and-backend-disposition]] (membership/storage), [[0011-blueprint-as-plan-of-record]].
  Resolves **OQ-8** and **OQ-9** in [`open-questions.md`](../open-questions.md).

## Context

The capture pipeline is specced (1A membership, 1C capture, 1G cron) but there is **no place that
tells the scheduler *which indices* to fetch**, nor where each index's operational metadata lives.
Concretely:

- `configs/universe.yaml` is a flat demo stub (`underlyings: [AAPL, MSFT, SPY]`) — not indices, no
  per-instrument IBKR contract reference, no fetch schedule.
- The **fetch time** was an *open decision*, not a design: the server-deployment plan defaulted to a
  single "US market hours" window (`server-deployment-plan.md` §4.3). That does not cover a
  multi-exchange universe — EURO STOXX 50 (Eurex, Europe) and the S&P 500 (US) close in different
  timezones, on different holiday calendars, with different half-days.
- 1A's `IndexConstituent` answers *what is **inside** an index* (point-in-time constituents, sourced
  from the broker). That is a **different concern** from *which indices we operate on and how we
  capture them*. Conflating the two would put look-ahead-sensitive bitemporal data and static
  operational config in one bag.

So adding an index today is **not** a one-line change — the mechanism does not exist. This ADR
specifies it. Two forks were put to the owner and ruled:

- **OQ-8 — where the registry lives.** Ruled: **extend `universe.yaml`** (vs a new `indices.yaml`
  bundle). It is the most blueprint-faithful home; Part VII already names `universe.yaml` for the
  monitored set + cadence, and keeping one hashed bundle avoids a new entry in the canonical bundle
  list.
- **OQ-9 — how to model market hours.** Ruled: a **per-index exchange-calendar code resolved by a
  calendar library** (vs literal hours in YAML). Literal hours rot on holidays/half-days/DST and are
  a look-ahead hazard at the close; a maintained calendar library handles all of it.

## Decision

### 1. The index registry is an `indices:` block in `universe.yaml`

`universe.yaml` grows a keyed `indices:` map. Each entry names one index the platform tracks:

```yaml
# configs/universe.yaml
version: "2026.06"
exchange: SMART                 # legacy default for the flat underlyings list (kept)
underlyings: [AAPL, MSFT, SPY]  # demo single-names (unchanged for now)

indices:
  SX5E:                         # key = the index symbol (the 1A/1C `index` key)
    name: "EURO STOXX 50"
    calendar: XEUR              # exchange_calendars code; scheduler derives the close from it
    currency: EUR
    ibkr: { conid: 0, secType: IND, exchange: EUREX }   # IBKR contract ref (conid TBD at impl)
    enabled: true
  SPX:
    name: "S&P 500"
    calendar: XNYS
    currency: USD
    ibkr: { conid: 0, secType: IND, exchange: CBOE }
    enabled: true
```

- **The key is the index symbol** (`SX5E`, `SPX`) — the same `index` key 1A's `members(index,
  as_of_date)` and 1C's capture consume. One vocabulary across membership, capture, and the front.
- **`enabled`** is the on/off switch the scheduler reads. Adding an index = add an entry; flipping
  `enabled: false` parks it without deleting history.
- **`calendar`** is an `exchange_calendars` MIC code (see §2), not literal hours.
- **`ibkr:`** is the *provider resolution* sub-block (conid / secType / exchange). The
  symbol/name/calendar/currency fields are **provider-agnostic** (they describe the index); only the
  `ibkr:` sub-block is IBKR-specific. A future Saxo/Deribit listing of the same index adds a sibling
  sub-block (`saxo:` / `deribit:`) under the same key — the platform stays multi-provider (ADR 0023).
  `conid: 0` is a placeholder; real conids are verified against IBKR at 1J impl time.

### 2. Per-index fetch time comes from an exchange-calendar library, not literal hours

The scheduler resolves each enabled index's **session close** from its `calendar` code via the
**`exchange_calendars`** library (new dependency, added with `uv`). The library owns timezones, DST,
holidays, and half-day early-closes; the cron fires the capture **after** that index's close on each
of its sessions. `XEUR` (Eurex) and `XNYS` (NYSE) are the starting codes for SX5E and SPX; the exact
code per index is a config value confirmed against the library's calendar set at impl (Cboe-listed
index options may warrant `XCBO`).

This **closes the server-deployment plan's run-window open decision** (§4.3): the run window is no
longer a single hand-written "US market hours" guess — it is **per-index, calendar-derived**, which
is the only correct answer for a multi-exchange universe.

### 3. Membership (1A) stays separate; the registry feeds it the index set

The registry says *which indices*; 1A's `IndexConstituent` says *what is inside each* (point-in-time,
bitemporal, look-ahead-gated). 1A resolves `members(index, as_of_date)` for exactly the registry's
enabled indices. The two never merge: the registry is static operational config (hashed); membership
is effective-dated reference data in Parquet (ADR 0034 §5).

### 4. Hashed vs operational split (ADR 0028)

The `indices:` block is **hashed** — it lives in `universe.yaml`, already a hashed bundle
(`config_hashes["universe"]`), because it changes *which records exist* (enabling SX5E means SX5E
snapshots/bars start landing). The **cron firing time is NOT a hashed literal** — it is *derived* at
run time from the index's calendar, so a holiday-table update in the library does not churn the
config hash. What is hashed is the `calendar` code (the choice of calendar), not the resolved times.

## Alternatives considered (rejected)

- **A dedicated `configs/indices.yaml` bundle** — conceptually cleaner separation, but adds a new
  entry to the canonical Part VII bundle list and a new `config_hashes["indices"]`; the blueprint
  already routes the monitored set + cadence to `universe.yaml`, so extending it is the lower-friction,
  more faithful choice. (OQ-8 ruling.)
- **Literal hours in YAML** (`SX5E: { tz: Europe/Berlin, close: "17:30" }`) — fragile: holidays,
  half-days, and DST must be hand-maintained, and a stale entry silently captures the wrong close
  instant (a look-ahead bug, not a cosmetic one). (OQ-9 ruling.)
- **A single global run window** (the old "US market hours" default) — wrong by construction for a
  multi-exchange universe; it would capture SX5E at the US close, not the Eurex close.
- **Putting the IBKR conid in the provider capture config only** (`infra-ibkr/configs/capture.yaml`)
  — keeps universe.yaml provider-agnostic but splits one index's definition across two files; the
  `ibkr:` sub-block under each registry entry keeps the index's definition in one place while still
  marking the provider-specific part as such.

## Consequences

- **New task:** [`tasks/1J-index-registry.md`](../../tasks/1J-index-registry.md) — land the typed
  `IndexRegistry` config object, the calendar resolver, and the `universe.yaml` `indices:` block.
  Foundational, sits with P0/D1 as a pre-req to 1A/1C/1G.
- **New dependency:** `exchange_calendars` (via `uv`). Note this is a deliberate exception to ADR
  0032's "zero new deps for the timer" — the dep belongs to the registry/calendar layer (1J), not the
  timer mechanism itself.
- **Tasks updated** to consume the registry: 1A (resolve membership for the registry's enabled
  indices), 1C (capture iterates enabled indices; close instant per the index calendar), 1G (the
  timer schedule is per-index/per-calendar, derived from the registry).
- **OQ-8 and OQ-9 → Resolved**, pointing here. The server-deployment-plan run-window open decision is
  closed (per-index calendar-derived).
- **Deferred to impl:** real IBKR conids (placeholders today); whether SX5E/SPX flip to `enabled:
  true` is gated on D1's `provider` segment (equity capture at scale, ADR 0034 §4) — the registry can
  list them disabled before then.
- The actual `configs/universe.yaml` edit (adding the live `indices:` block) lands with 1J, not in
  this doc-only change, so no hash churns before the consuming code exists.
