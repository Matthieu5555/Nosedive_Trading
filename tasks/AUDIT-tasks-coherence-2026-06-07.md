# Provisional audit — task-spec coherence (2026-06-07)

**Status: provisional findings, no fixes applied.** This file records inconsistencies
found in an audit of every spec under `tasks/`, checked against the blueprint
(`documentation/blueprint/`, the absolute reference per ADR 0011), the ruling ADRs
(`.agent/decisions/`), the plan-of-record roadmap, and against each other. Each item
names the conflicting sources so a fix can be scoped later. Nothing here has been changed
in the specs or the code yet.

**Method.** 23 task files audited in parallel (one agent each) against a canonical digest
built from the blueprint + ADRs 0011–0035 + the roadmap, then three cross-task passes
(field/schema/units, layer/ownership, sequencing/dependencies). The four HIGH items below
were re-verified by hand against the working tree before this file was written. Scope was
the *specs*, not a full code audit — though a few specs describe code that is already on
disk and wrong (see §A item 6).

Severity: **HIGH** = will produce wrong numbers, a layering violation, an ADR/number
collision, or send an implementer building against a false premise. **MEDIUM** = a real
inconsistency that misleads but is recoverable. **LOW** = wording / dead link / cosmetic.

---

## UPDATE — re-triage 2026-06-07 (later same day)

Between writing this audit and now, an implementing agent advanced Phase 0 + much of Phase 1
**directly in the working tree on `main` (all uncommitted)**. A read-only re-check shows it has
**already resolved the three structural HIGH items in code/ADRs — generally more cleanly than the
fix directions below** — but the **task specs were not updated to match**. Findings now fall in
three buckets:

- **A — fixed in code, spec drifted (owner decision: leave the specs as-is for now).**
  - **#1 ADR 0035 collision** → ADRs `0036-dollar-greek-units-and-monetization-conventions.md`
    and `0037-futures-capture-deferred-forward-only.md` now exist. Code/ADRs ✅. Specs P0
    (`:75,85,169,227,271`) and 2A (`:29,218`) still say "ADR 0035" — stale, left as-is.
  - **#2 dollar-Greek convention** → new `packages/infra/.../pricing/dollar_greeks.py` implements
    OQ-1 *configurably*: `dollar_gamma = Γ·S²/100` (`one_pct` default) vs `Γ·S²` (`one_dollar`);
    `dollar_theta = θ·mult/day_count` (365 default vs 252); `dollar_rho` + unit strings. They built
    a **separate analytics home** instead of patching `risk/greeks.py` (unchanged — still per-$1).
    Resolved for the analytics path; the "two homes" tension stays a **live design point for
    Phase 2** (2A/2C/2D must consume `pricing/dollar_greeks.py`, not `greeks.py`).
  - **#3 1J config layer** → resolved correctly: raw `indices` block rides on
    `core.config.UniverseConfig.indices` (core blind to the calendar lib); typed validation in
    `infra/universe/registry_loader.py` + `index_registry.py`. No `infra/config/` created. Spec 1J
    still says `infra/config/` — stale, left as-is.
- **B — not yet implemented, spec is what gets built (act later, after the agent commits).**
  The high-value spec fixes: 2D's false "siblings don't exist" premise (#4), 3A's non-existent
  blueprint authority for the order domain (#5), `server-deploy-plumbing` broken code (#6),
  `FuturesPoint`/`forward_price` name collision (#9), TESTING.md missing as-of test (#14), 2C
  component ownership (#15), and the Phase-2 specs needing to repoint at `dollar_greeks.py` / ADR
  0036. These were **not** edited (agent still active on the shared tree — collision risk).
- **C — possibly baked wrong into the new code (verification deferred, owner decision).**
  Needs a *code* re-audit, not a spec read: does 1F/2A actually consume `dollar_greeks.py`
  consistently? is the index symbol SP500-vs-SPX consistent in the landed code? does
  `actor/close_capture.py` honour as-of / no-look-ahead? **Deferred until the agent's work commits.**

**Decisions taken (owner, 2026-06-07):** agent still active → edit nothing on the shared tree;
leave drifted implemented-task specs untouched; defer the code-level verification. Next action is
to wait for the agent's commit, then re-audit the **code** (bucket C) and address bucket B.

The sections below are the **original spec audit**, kept verbatim as the record; read each item
through the bucket A/B/C lens above.

---

## A. HIGH — must resolve before the affected task is built

### 1. ADR number 0035 is double-allocated (P0 ↔ existing 0035 / 1J)
- **Where:** `tasks/P0-contracts-and-unblockers.md:74-75, 85, 149, 169, 227, 271`
  vs `.agent/decisions/0035-index-registry-and-per-index-capture-schedule.md` (Status: **accepted, 2026-06-07**).
- **Problem:** P0 says "0034 is the current head, next free number is **0035**" and assigns
  the OQ-1 $-Greek ADR to **0035** and the futures decision to **0036**. But 0035 already
  exists on disk as the **index-registry ADR** that **1J is built against**. Following P0
  verbatim either overwrites an accepted ADR or creates a duplicate number. P0's own Gotcha
  ("confirm no one else has claimed those numbers") is the check that failed.
- **Blast radius:** 2A depends on "P0.2 → ADR 0035" for the $-unit contract; 2B/2C/2D inherit
  the chain. Multiple Phase-2 tasks point at the wrong ADR.
- **Fix direction:** renumber OQ-1 $-Greeks → **0036**, futures → **0037** (after re-confirming
  the head); update P0 Tasks 2 & 4, the numbering note, and 2A:29 + any 2B/2C/2D reference.
  Reserve 0035 for the index registry as already accepted.

### 2. Two contradictory dollar-Greek conventions, both claimed as the single "home"
- **Where:** `risk/greeks.py:18-20, 94-103` (built) vs `P0:79-106`, `1F:56-60, 100-104`,
  `2A:104`, and consumed by `2C`, `2D`.
- **Problem:** P0.2 / 1F / 2A pin the analytics dollar-Greeks to **per-1%-move / per-day-count**:
  `dollar_gamma = Γ·S²/100`, `dollar_theta = Θ·mult/365`. The **already-built** `risk/greeks.py`
  implements a **different** convention: `dollar_gamma = γ·S²·mult·qty` (**no `/100`**),
  `dollar_theta = θ·mult·qty` (**no `/365`**), and has **no `dollar_rho`**. 1F simultaneously
  states the `/100` formula *and* instructs "reuse `greeks.py`, don't fork" — i.e. it mandates
  reusing a function whose gamma output differs from its own stated formula by a factor of 100.
  `dollar_vega` (×0.01) is consistent across all; only **gamma and theta diverge**.
- **Blast radius:** 2A/2D sum dollar-Greeks pulled from 1F's `ProjectedOptionAnalytics`
  (per-1% / per-365) while 2C/2D reuse `PositionRisk.dollar_*` (per-$1 / no-365). Summing
  across Tab-2 mixes units — a 100× error on gamma. This is the single most consequential
  numerical inconsistency in the set.
- **Note (1F internal):** the change 1F frames as "additive (`dollar_theta`/`dollar_rho`)" is
  actually a **Category-A economics change** to an already-built risk module (it alters
  existing numbers), not additive.
- **Fix direction:** make `gamma_normalisation=one_pct` + `theta_day_count=365` the single
  authority and patch `risk/greeks.py` to read those config flags (route `/100` and `/365`
  through them), **or** explicitly document greeks.py vs 1F as two deliberate representations
  with a named conversion at the seam. Add one golden test asserting the chosen default so the
  two homes cannot drift. Do not let 2A claim "one home" until they actually agree.

### 3. 1J places the typed `IndexRegistry` config in `infra`, but its sibling `UniverseConfig` + the C7 loader live in `core`
- **Where:** `tasks/1J-index-registry.md:14-18, 51-57, 70` vs
  `packages/core/src/algotrading/core/config/platform_config.py:46` (`UniverseConfig`);
  `packages/infra/.../infra/config/` **does not exist**; `pyproject.toml:141-148` (layer order:
  `core` at bottom, `infra` above).
- **Problem:** 1J tells the implementer to build the typed config "beside the existing universe
  config (`infra/config/` — the C7 bundle-loader path, mirror `UniverseConfig`)". That directory
  doesn't exist and the C7 loader + `UniverseConfig` are in **core**, not infra. If the core
  loader must construct `IndexRegistry` while it sits in infra, **core would import infra** — a
  layering inversion `import-linter` forbids. (1J's calendar resolver in `infra/universe/` *is*
  correctly placed, because it wraps the infra-level `exchange_calendars` dep.)
- **Fix direction:** put the typed `IndexRegistry` config object in
  `packages/core/.../core/config/` alongside `UniverseConfig`; keep only the calendar resolver
  in `infra/universe/`. Correct every `infra/config/` path reference in 1J.

### 4. 2D is written on the false premise that 2A/2B/2C don't exist
- **Where:** `tasks/2D-strategy-composition.md:24, 27, 42, 204` vs
  `tasks/2A-basket-builder.md`, `tasks/2B-stress-scenario.md`, `tasks/2C-pnl-attribution.md`
  (all present, all linked from `TASKBOARD.md:81`).
- **Problem:** 2D states four times that its Phase-2 siblings "have no task files yet" and tells
  the implementer to cross-ref them "by intent, do not cite code from them." They are fully
  specced: 2A freezes the basket contract, 2B the ±50%/±50% grid + `/api/risk/scenarios` seam,
  2C the per-Greek attribution shape. 2D's whole dependency narrative is built on a false
  premise and would misdirect the implementer to re-invent already-frozen seams.
- **Fix direction:** rewrite 2D's dependency section to cite the concrete contracts/seams 2A/2B/2C
  freeze, and drop the "build against intent / pin once they freeze" language.

### 5. 3A says the blueprint governs an order-ticket domain the blueprint does not define
- **Where:** `tasks/3A-order-ticket.md:17-18, 61-63, 95-97, 114-116, 162-164` vs
  `documentation/blueprint/**` (no order / side / qty / price-spec / TIF / routing fields;
  `09-data-dictionary.md` has zero order fields; the blueprint is explicitly "blind to alpha").
- **Problem:** 3A asserts five times that ADR 0011 / the blueprint "governs leg semantics,
  side/qty conventions, the price spec, time-in-force" and mandates a test
  (`test_ticket_payload_uses_blueprint_field_names`) asserting "the blueprint/ADR-0011 field
  names." The blueprint defines **none** of these. The implementer will find nothing to conform
  to; the test becomes a tautology asserting against whatever names the implementer chose —
  contradicting house style (expected values independently derived, not from the code under test).
- **Fix direction:** 3A must define the order-ticket contract itself (it is genuinely new domain,
  outside the blueprint's strategy-agnostic scope), stop claiming blueprint authority for these
  names, and rewrite the field-name test to assert against 3A's own pinned contract (or fold it
  into the 3B `SignedTicket` binding). Same caveat applies, more mildly, to 3B (§C).
- **Layer caveat:** 3A places the ticket in `packages/infra/.../orders/`. An order ticket is
  trading intent; the blueprint fences that off from infra ("keep infrastructure opaque w.r.t.
  trading intent", `19-final-reminders.md`). Either justify tickets as alpha-free plumbing or
  move them to `execution`. 3B already lives in `packages/execution`. See §C item 3B-3.

### 6. `server-deploy-plumbing` ships code that does not run (already on disk)
- **Where:** `scripts/ibkr_bootstrap.py:91, 118`; `packages/infra-ibkr/pyproject.toml:20`.
- **Problem (two real bugs, reproduced):**
  - `from algotrading.infra.connectivity import client_id_for` — `client_id_for` is **not
    exported** from that package (it's a method on `BrokerConfig`, `supervisor.py:101`). `mypy`
    errors (`attr-defined`); with `IBKR_CLIENT_ID` blank in `.env.example`, the default path
    hits exactly this broken branch → runtime `ImportError`.
  - `from ib_async import Stock` (and `ibkr_transport.py:15` `from ib_async import IB`) but the
    `ibkr` extra is `nautilus-trader[ib]`, which ships **`ibapi`, not `ib_async`** →
    `ModuleNotFoundError` under `--extra ibkr`. The import is caught and returns `_HARD`, so a
    reachable Gateway **still exits 1**, breaking the task's own acceptance criterion ("clean
    session exits 0"). The doc `connect-providers.md:37` already flags `ib_async` "not yet declared."
  - Acceptance "ruff/mypy clean on the new script" is self-inconsistent: the root gate **excludes**
    `scripts/` (so "gate stays green" is trivially true and says nothing), and running mypy on the
    script directly is **not** clean.
- **Fix direction:** load `BrokerConfig` via `load_broker_config` and call
  `broker_config.client_id_for('smoke')`; either declare `ib_async` in the `ibkr` extra or import
  `ibapi`; reword the acceptance criterion. Mark "Depends on: nothing" as false (real unmet runtime deps).

---

## B. MEDIUM — real inconsistencies, recoverable

### 7. S&P 500 symbol: `SPX` (registry) vs `SP500` (capture/storage)
- **Where:** `1J:46, 91, 106` (`SPX`) vs `1C:27, 166` and `D1:14` (`SP500`); `1A:16` hedges
  "`SP500`/`SPX`".
- **Problem:** the index symbol is the registry key 1J supplies to 1A/1C/1G/1I and the
  `underlying` value in partition paths + the membership resolver. Two spellings would split
  partitions and break registry/resolver lookups. `SX5E` is consistent everywhere; only the US
  index diverges.
- **Fix direction:** pick one canonical symbol (1J seeds `SPX`), record it in the P0
  data-dictionary / 1J registry as the single allowed key, replace every `SP500` in 1A/1C/D1.

### 8. ADR-0034 §4 partition key defined two ways (physical dirs vs logical key)
- **Where:** `D1:35, 41` (physical: `provider/trade_date/underlying[/version]`; "code_version/
  config_hash stay in the ProvenanceStamp + manifest, **not** partition dirs") vs `V1` and
  `TASKBOARD` (both list the partition key as `(provider, underlying, trade_date, code_version,
  config_hash)`).
- **Problem:** the same §4 layout is described with code_version/config_hash as directories in
  V1/TASKBOARD but explicitly *not* as directories in D1. (Separately: P0 gives `DailyBar` PK
  `(provider, underlying, trade_date)` and 1D gives `FuturesPoint` PK adding `maturity_years` —
  those are fine, just distinct grains.)
- **Fix direction:** make D1 the single source of truth for the physical layout; have
  V1/TASKBOARD cite it verbatim and separate the *logical* ADR-0017 key from the *physical*
  directory segments, documenting code_version/config_hash as provenance/manifest fields.

### 9. `FuturesPoint` reuses the field name `forward_price` for a captured futures price
- **Where:** `1D:59-60` (`FuturesPoint` "mirrors `ForwardCurvePoint`... the captured futures
  price") vs `forward_price` = the ADR-0029 name for the **option-implied derived forward** used
  by 1F/2A and listed in `TESTING.md`.
- **Problem:** 1D's whole purpose is to keep captured futures distinct from the derived forward
  and reconcile them — but it names both `forward_price`, so the reconciliation would compare two
  fields with the same name denoting different quantities.
- **Fix direction:** give `FuturesPoint` a distinct field (`futures_price` / `settle_price`); pin
  it in P0.4's ADR + the data dictionary.

### 10. 1G claims `run_end_of_day()` already emits the per-run manifest; it doesn't
- **Where:** `1G:55-58` vs `orchestration/pipeline.py:99-200` (records only `StageRun` via
  `record_stage`, no manifest) and `observability/runner.py:11` (the manifest path is a *separate*
  module; run_state is "the durable, manifest-keyed twin", i.e. explicitly not the manifest path).
- **Problem:** the "reproducible from its manifest, not merely traceable through the JSONL ledger"
  guarantee has no existing emission step in the callable 1G invokes. The runner (which 1G owns)
  would have to wire manifest emission itself.
- **Fix direction:** restate manifest emission as a runner responsibility, not an existing
  pipeline step.

### 11. 2C builds attribution terms from scenario shocks but names `greeks.py`'s `dollar_*` as their basis
- **Where:** `2C:48-49, 184-186` vs `greeks.py:94-99` and `scenarios.py:199-206`.
- **Problem:** 2C's term formulas (matching `_taylor_pnl`) use the **actual scenario shocks**
  (`vega·vol_shock·scale`, `½·Γ·(S·spot_shock)²·scale`); `greeks.py`'s `dollar_*` use **fixed**
  normalizations (`vega·0.01`, `Γ·S²`, no ½). Building attribution from the `dollar_*` properties
  would break 2C's own requirement that `sum(terms) == local_approx_pnl` exactly. The invariant
  2C actually needs is the ADR-0029 *naming*, not the greeks.py *numeric* convention.
- **Fix direction:** reword 2C to derive terms from the Taylor expansion with scenario shocks;
  cite ADR-0029 naming, not the greeks.py numbers. (Related to §A item 2.)

### 12. New cross-workstream contracts not routed through the M0 owner
- **Where:** `2C:106-109` (needs a new attribution seam — existing `ScenarioResult` has only
  `scenario_pnl`); `D1:8-9, 46-51` (adding `provider` to frozen analytics contracts); both vs
  `documentation/interface-contracts.md:48, 69, 94` (frozen contracts are M0-owned; new fields
  additive and routed through M0).
- **Problem:** both tasks mint/extend frozen contracts without stating the change must be routed
  through the M0 contracts owner — risk of an infra task minting a frozen cross-workstream contract
  outside governance.
- **Fix direction:** add an explicit "route through M0, additively" step to both.

### 13. V1 hard-asserts the ADR-0034 §4 provider layout that D1 has not yet built
- **Where:** `V1:64, 166` ("assert the raw partition landed under the §4 provider-partitioned
  layout") vs `ADR 0034:81` ("`provider` as a partition segment is **not yet implemented**...
  today's key is `trade_date × underlying [× version]`", deferred to D1). Confirmed: no `provider`
  in `storage/partitioning.py`.
- **Problem:** V1 is otherwise emphatically "SKIP unlanded stages", but this one stage hard-asserts
  a layout that doesn't exist yet → Stage 1 fails today, contradicting V1's own SKIP discipline.
- **Fix direction:** SKIP-gate the partition assertion on D1 landing, or accept today's
  `trade_date × underlying` key until D1 lands.

### 14. TESTING.md has no named test obligation for the no-look-ahead / as-of invariant
- **Where:** `TESTING.md:104-117` (property tests: parity, calendar no-arb, Greeks signs,
  monotonicity, reordering — but **no** as-of/look-ahead entry) vs `conventions.md:87-92`
  ("No look-ahead bias, ever... all data access through an as-of abstraction") and the
  membership/daily-bar as-of gates (OQ-3, ADR 0034).
- **Problem:** by the doc's own thesis ("an agent tests what is named and skips what is not"), the
  single most consequential financial-correctness invariant is the one left unnamed.
- **Fix direction:** add an owned property test that an as-of join never returns a record dated
  after the as-of timestamp (most naturally A- or C-owned).

### 15. 2C's attribution-waterfall React component is owned by no task
- **Where:** `2C:17, 109` (delegates the React/Plotly to 1I) vs `1I:3, 8, 21` (1I scopes itself
  to the Tab-1 Home page + reusable seams; "Tab 2 reuses these components" — it doesn't claim
  Tab-2 task pages). 2A/2B/2D each own their own Tab-2 UI; 2C is the lone outlier disclaiming its.
- **Fix direction:** make 2C own its attribution-waterfall web component (reusing 1I's
  components/BFF), or have 1I explicitly take all Tab-2 React pages. Close the disclaim/no-claim gap.

---

## C. LOW — wording, dead links, cosmetics

- **P0:** labels the futures decision "OQ-4 futures fork" — OQ-4 is *only* the tenor grid;
  futures is a separate unnumbered row. (`P0:141, 169`)
- **D1:** "State going in" overstates that a `ProviderFlow` *protocol* + per-provider
  `resolve_config` already exist — only `run_provider_flow` (a function) exists; no
  `class ProviderFlow` / `def resolve_config` anywhere in `packages/`. The blocking dependency
  conclusion may still hold, but the state-of-world claim is wrong. (`D1:12-13, 20`)
- **1A:** proposed `index`-first partition segment isn't in D1's prescribed layout (no `index`
  segment defined); defers to D1's registry decision, so a wording gap. (`1A:55-57`)
- **1D:** `FuturesPoint` PK declares `provider` while mirrored `ForwardCurvePoint` has no
  `provider` field (it's partition-only) — reconcilable but ambiguous; and PK uses `trade_date`
  where the blueprint `forward_curve` family keys on `snapshot_ts` (sensible for daily grain, but
  note `snapshot_ts` stays the canonical alignment field). (`1D:60, 65`)
- **1F:** `dollar_gamma`/`dollar_theta` diverge from bare blueprint Eq 17/18 — relies on the OQ-1
  override (correctly flagged); load-bearing context for item 2. (`1F:57-59`)
- **1G:** `systemctl --user` timers on a headless box need `loginctl enable-linger` or
  `Persistent=true` catch-up silently won't fire; never mentioned. Also depends on three 1J
  accessors (`enabled_indices`/`is_session`/`session_close`) that don't exist yet, with no stub
  fallback stated (unlike the 1C seam). (`1G:78, 45-48`)
- **1I:** references `DailyBar` (not yet built — 1C owns it) and cites it as "(1C/1E)" while 1E is
  framed a no-op; cosmetic dangling. Confirm 1C's frozen `DailyBar` field names match 1I's six
  (`trade_date/open/high/low/close/volume`). (`1I:47-53, 48`)
- **2B:** cites "ADR 0034 §1" for "serving is read-only; cron is sole writer" — §1 is about
  Postgres being optional/unused; the rule isn't stated there (it's sound, just mis-cited).
  (`2B:204`)
- **2C:** `ScenarioConfig` is in `core` (`platform_config.py:214`), not co-located with
  `RiskParams` in `infra/risk/config.py`; "reuse the same home" is imprecise. (`2C:102-103`)
- **2D:** cites "C7 DI pattern" but C7 is archived (`tasks/archive/C7-config-hardening.md`); point
  to ADR 0028 instead. (`2D:102`)
- **3A:** dead/wrong ADR links — `0025-ibkr-first-broker-increment.md` (actual:
  `0025-nautilus-host-catalog-topology.md`, and the parenthetical mischaracterizes its subject),
  `0023-nautilus-runtime-spine.md` and `0024-ibkr-rest-and-tws-transport.md` (wrong slugs). (`3A:19`)
- **3B:** invents `SignedTicket`/`TransmissionDecision` with no blueprint anchor (unavoidable for
  execution; defers to ADR 0011 + 3A correctly); paraphrases ADR 0024 §4 as "separate gated-send
  framing" — §4 only states the read-only invariant; and "registered like the other contracts" is
  under-specified given the registry is in `infra` while 3B's types are in `execution` (must be a
  runtime call execution→infra, not types placed in the infra package — else layering inversion).
  (`3B:15, 51, 64`)
- **security-review:** minor file:line drift in a few citations (`verify_tls` at :34 not :33; CORS
  ~:48 not :46-50) — every substantive claim verified true. (`security-review:62-64, 88-90`)
- **TESTING.md:** replay-byte-identical seam doesn't call out pinning `code_version` + `config_hash`
  as the controlled variables (hardening note). (`TESTING.md:98`)
- **TASKBOARD:** 1E framed three ways ("no-op" / "folded into P0/1C" / "add a DailyBar OHLC
  contract"); the `DailyBar` contract is real mandatory work — state explicitly which spec (P0 or
  1C) owns it so it isn't left unowned. (`TASKBOARD:68, 80`)

---

## Cross-cutting themes (for the fix discussion)

1. **Dollar-Greek convention is the deepest fault line** (items 2, 11, and the 1F/1D LOW notes):
   `risk/greeks.py` was built to bare blueprint Eq 17/18; P0/1F/2A then pinned the OQ-1 per-1% /
   per-365 convention on top without reconciling the built module. Decide the canonical default
   once, make greeks.py read the config flags, golden-test it — everything Tab-2 sums depends on it.
2. **ADR numbering raced** (item 1): 0035 was accepted the same day P0 claimed it as free. A quick
   "head check before assigning a number" habit (P0's own Gotcha) would have caught it.
3. **core-vs-infra layer discipline for typed config** (item 3, plus 2C-LOW): the C7 config bundle
   lives in `core`; specs keep reaching for an `infra/config/` that doesn't exist. Library-wrapping
   resolvers go in infra; hashed typed-config objects go in core.
4. **Specs written before/independently of siblings drift** (items 4, 6, 10, D1-LOW): several "state
   going in" / "depends on nothing" claims are stale or wrong. A spec asserting an existing code
   capability should be the thing that's grep-verified before the claim ships.
5. **M0 contract governance** (item 12) keeps being skipped when a task needs a new frozen field.
