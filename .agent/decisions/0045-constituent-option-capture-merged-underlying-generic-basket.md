# 0045 — Constituent option capture: one underlying-generic basket, top-N seam stubbed

- **Status:** ⛔ **SUPERSEDED by [[0051-return-to-blueprint-dispersion-realized-vol-diagnostic]]
  (2026-06-15).** Constituent-option capture baked a strategy choice into the immutable raw layer
  (permanent option-history loss + the serial-capture throughput crisis) for a feature the
  blueprint computes from realized constituent vol instead. The lane is retired; ρ̄ moves to
  realized vol. Implementation deferred until after the 2026-06-15 evening close. ~~accepted,
  2026-06-14. Lands in `packages/infra-ibkr/src/algotrading/infra_ibkr/
  collectors/cp_rest_constituent_capture.py`, generalising `cp_rest_close_capture.py`.~~
- **Date:** 2026-06-14.
- **Implements:** TARGET §0 (universe = one enabled index + its top-N by weight, point-in-time) +
  §3 S1 (dispersion) + §7.4 ("the single biggest new lane") under
  [[0011-blueprint-as-plan-of-record]].
- **Relates to:** [[0042-index-options-only-scope-ibkr-sole-broker]] (index-options-only, IBKR sole
  broker, SX5E sole live index — the scope this stays inside),
  [[0035-index-registry-and-per-index-capture-schedule]] (the `IndexEntry` / `constituent_conids`
  this resolves over), [[0024-cp-rest-transport]]/[[0031-cp-rest-settled]] (the transport this
  reuses, not a new Nautilus node), [[0033-asof-join-membership]] (the point-in-time 1A resolver
  the top-N reads). **Depends on** the parallel `infra-sx5e-weighted-membership` task for the shared
  `top_n_by_weight` resolver (not yet merged — see "The stubbed seam").

## Context

Capture ran at the **index** level only: `collect_live_basket` was hard-bound to an `IndexEntry`
and banked one index's option chain. The S1 dispersion book (and the R3 implied-correlation signal
layer) need per-name option **surfaces** for the index's top-N constituents by weight, point-in-time
and registry-driven — the constituents become option underlyings at this phase. Constituents today
have OHLC bars only (`ohlc-constituent-backfill`), not chains.

Two facts shaped the design:

1. **The analytics engine is already underlying-generic.** `IvPoint` / `SurfaceParameters` /
   `projection` / `valuation_join` all key on `underlying` (`underlying_symbol`). So widening the
   *universe captured* needs **no engine change** — only more underlyings fed into the same
   `run_analytics`.
2. **The index capture and a constituent capture differ only in a small descriptor.** Both resolve
   a conid, snapshot a spot, discover + plan + budget a chain, snapshot the close, assemble a
   basket. The only differences are symbol / search-symbol / exchange / currency / underlying
   sec-type (`IND` vs `STK`) / whether the conid is pre-resolved (a pin) or resolved at fire time.

## Decision

**Factor the capture over a `CaptureTarget` descriptor and merge the index + top-N constituents
into one underlying-generic `IndexBasket`.**

- `cp_rest_close_capture.collect_target_basket(target, conid, months, …)` is the capture body,
  parameterised by a frozen `CaptureTarget`. `collect_live_basket` becomes a thin index wrapper
  over it (behaviour byte-identical — the prior `test_cp_rest_close_capture.py` passes unchanged).
- `cp_rest_constituent_capture.collect_index_and_constituents_basket(...)` captures the index leg
  (the **spine** — its failure fails the fire), resolves the **point-in-time top-N by weight**
  (N = `UniverseConfig.constituent_top_n`, from 1A `members` — never a hand-set list, never today's
  membership for a past date), resolves each constituent's equity conid (verified
  `constituent_conids` pins first, then a `STK` secdef search — the exact `history_backfill`
  pattern), captures each chain on the **same** grid / selection / close instant, and concatenates
  all legs into **one** `IndexBasket` keyed by `underlying`.
- **A per-constituent failure is non-fatal** (no listed options / unresolvable conid / capture
  error → logged + skipped), mirroring the OHLC backfill. One bad name never aborts the fire; the
  index spine does.
- **N is hashed economic config** (`UniverseConfig.constituent_top_n`, default course-10): it
  decides which constituent names land records, so it folds into `config_hashes["universe"]`, never
  a `.py` literal (ADR 0028).
- **Transport unchanged** (CP REST, ADR 0024/0031): no new Nautilus node; one injected transport
  drives the whole widened capture in the gate with no network and no secrets.

Alternatives rejected: (a) one `IndexBasket` *per underlying* threaded separately through the
runner — needs a runner/stage change for no analytics benefit (the engine already partitions by
underlying); (b) a parallel constituent capture path duplicating the discovery/snapshot mechanics —
the exact drift the `CaptureTarget` factoring exists to prevent.

### Option months for a pinned, ambiguous ticker

A `constituent_conids` pin exists *because* the bare ticker resolves to two listings (Euronext-Paris
`SAN`=Sanofi vs Bolsa-de-Madrid `SAN`=Santander; IBKR renames one `SAN1`). The conid is resolved
from the pin (no search), but the listed option **months** still must be read. Reading them by a
symbol search would hit the same ambiguity. So `option_months_for_conid` issues a **conid-keyed**
secdef search and matches the row by conid (`parse_option_months_by_conid`) — the unambiguous
identifier — never by the shared ticker.

## The stubbed seam (wire on merge)

The point-in-time **top-N-by-weight selector** is owned by the parallel `infra-sx5e-weighted-
membership` task as a pure resolver `top_n_by_weight(store, index, as_of_date, n)` in
`algotrading.infra.universe`. That task is not yet merged here, so this lane carries a **minimal
local stand-in** `_top_n_by_weight` built on the landed `members` + `basket_weight_sum`: it ranks
the as-of basket by weight (descending, deterministic name tie-break) and **rejects a basket with
any missing weight** with a labeled `MembershipError` (you cannot rank what you do not know — the
economic-correctness trap of silently dropping unweighted names). On merge: delete
`_top_n_by_weight`, import the shared `top_n_by_weight` — the call-site signature is identical, so
the swap is one import line.

## Consequences

- **S1 dispersion / R3 implied-correlation are unblocked** for capture: a close run banks option
  chains + surfaces + Greeks for the index's top-N constituents on the same grid as the index.
- **No engine change**; the widened basket flows through `run_analytics` / `project_grid` / persist
  exactly as the index-only basket did.
- **Production wiring:** `live_basket_source(..., store=...)` routes to the widened capture; the
  `scripts/eod_run.py` shim passes the runner's canonical-`data_root` store, so the membership read
  is the membership the platform banked. With no store the source stays index-only (back-compat).
- **A weighted SX5E membership snapshot must be banked first** (`scripts/ingest_membership.py`);
  with none banked the fire degrades to the index leg only (logged), never crashes. The SSGA/FEZ
  weight CSV already in `configs/index_weights/` is the honest free proxy until the OQ-3 source.
- **Scope held** (ADR 0042): index-options-only, IBKR sole broker, SX5E sole live index. No futures,
  no second broker, no second live index introduced.
- **Not done in this lane:** the shared `top_n_by_weight` resolver (parallel task) and a *banked*
  weighted SX5E snapshot under the canonical store; no live IBKR capture was run (no credentials in
  this environment — built and tested against the fake gateway, stated plainly).
