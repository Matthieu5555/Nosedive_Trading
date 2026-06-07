# Code-level audit — post-Phase-2 landings (2026-06-07)

**Method.** Multi-agent fan-out audit of the *landed* code (the bucket-C code re-audit the
[coherence audit](AUDIT-tasks-coherence-2026-06-07.md) deferred until the Phase-1/2 work committed),
plus a re-check of its bucket-B spec items. Five finders (one per dimension) over the working tree;
every finding then verified by **two independent adversarial lenses** — *factual* (is the cited code
literally true?) and *impact* (does it cause a wrong number / violation / false-premise, or is it
true-but-harmless?). 17 agents total.

**Headline: Phase 2 landed clean. No active defect.** Raw 6 findings → **1 confirmed** (a spec doc),
**5 contested** (factual-true but impact-harmless: dead code, parked specs, observability nits),
**0 confirmed code bug**. Critically, **the ×100 dollar-greek risk is NOT present in live code**.

---

## The reassuring negative: no live ×100 gamma error

The most consequential dimension came back clean. Every landed Phase-2 module takes its dollar-greeks
from the **canonical** home (`pricing/dollar_greeks.py`, per-1% gamma) or fields already derived
through it — verified by both lenses:
- `risk/multileg.py` (2A) sums `ProjectedOptionAnalytics.dollar_*` (the canonical, projection-derived rows).
- `risk/attribution.py` (2C) and `risk/stress_surface.py` (2B) use **full reprice + Taylor terms**, not stored dollar-greeks.
- `risk/aggregation.py` sums `position_*` raw per-unit×scale, not `dollar_*`.
- The BFF (`serializers.py:218,244`) even rescales stored per-$1 gamma by `/100` to reach `one_pct`.

The old per-$1 `risk/greeks.py` `PositionRisk.dollar_gamma/theta` exist but are **dead code — read by
nothing**. So no aggregation mixes conventions today.

---

## Confirmed (both lenses agree — actionable)

| # | Dimension | Finding | Sev | Fix |
|---|-----------|---------|-----|-----|
| 1 | bucket-B spec | **2D still claims 2A/2B/2C "have no task files yet / do not exist"** (`tasks/2D-strategy-composition.md:24,27,41-42,204`) though all three are written **and landed** (2A `b2b6a06`, 2B done, 2C `4e3f50f`). Would misdirect the 2D implementer to re-invent already-frozen seams. | MED | Rewrite 2D's Depends-on / State / Gotchas to cite the concrete frozen contracts 2A/2B/2C define; delete the "no task files yet" language. |

## Contested = true-but-harmless (factual ✓, impact says no live harm)

| # | Dimension | Finding | Real impact |
|---|-----------|---------|-------------|
| 2 | dollar-greeks | `risk/greeks.py:95,103` `PositionRisk.dollar_gamma/theta` are off-convention (per-$1, no /365) | **Dead code** — read by no landed module. Dormant footgun, not a bug. **Hygiene: delete or route through `pricing/dollar_greeks.py`** so a future consumer can't ×100. |
| 3 | no-look-ahead | `cp_rest_close_capture.py:276` admits snapshot rows with no `_updated` (stamped at as_of by fiat) | **Not a look-ahead violation** — at-as_of stamps are always admissible downstream (`snapshots/as_of.py:49`). Freshness mislabel only. Optional: counted log mirroring `drop_post_close`. |
| 4 | bucket-B spec | `3A:17-18,61-63,162-164` asserts the blueprint governs order-ticket field names (it defines no order domain) | Doc imprecision. 3A also tells the builder to define the contract itself, and its test mirrors the working `test_basket_payload_uses_blueprint_field_names` pattern. **Fix when 3A is built.** |
| 5 | bucket-B spec | `1D:66` steers `FuturesPoint` to reuse `forward_price` for a captured futures price (collision with the derived forward) | 1D is **PARKED** (ADR 0037, no 1D code allowed) and already says "prefer a new `FuturesPoint`… conflating loses the distinction." **Pin `futures_price` when un-gated.** |
| 6 | bucket-B spec | `TESTING.md:103-117` property-test list has no named as-of entry | The as-of invariant **is** named + `check-lookahead-bias`-gated across ~17 specs (1A has explicit as-of cases). The property-section omission is cosmetic. Optionally add a pointer. |

## Clean dimensions (no findings)
- **Symbol consistency (SPX vs SP500):** the landed code uses **SPX consistently** — no split partitions / lookup breaks.
- **Contract governance:** the new Phase-2 contracts (Basket/BasketLeg, attribution seam, stress cells, 1F `atmp`) are frozen, slotted, registered, round-tripped, additive, blueprint-conformant names.

---

## Recommended actions (priority order)
1. **Fix 2D spec** (confirmed #1) — small doc edit; do before anyone builds 2D.
2. **Hygiene #2** — delete/retire `PositionRisk.dollar_gamma/theta` or route through `pricing/dollar_greeks.py`; kills the dormant ×100 footgun before Phase-3 aggregation grows.
3. **#4/#5** — fold into 3A build / 1D un-gating respectively (not now).
4. **#3/#6** — optional observability/doc nits.

**Bottom line:** Phase 2 is structurally sound; nothing blocks building 2D / Phase 3.
