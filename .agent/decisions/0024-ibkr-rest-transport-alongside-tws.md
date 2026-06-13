# 0024 — IBKR over REST as a course requirement, under the Nautilus spine

> **AMENDED 2026-06-13 (T-index-only-refactor).** Saxo and Deribit were removed entirely —
> **IBKR is the sole live broker** and the app is index-options-only (SX5E first, SPX parked;
> [ADR 0042](0042-index-options-only-scope-ibkr-sole-broker.md)). The "same case as Saxo/Deribit"
> / "Saxo/Deribit precedent" reasoning below stands only as the *historical pattern* that
> justified the custom IBKR-REST adapter — there is no Saxo/Deribit adapter to match anymore. The
> decision itself (build the custom IBKR-REST connector under the Nautilus spine, REST preferred)
> is unchanged.

- **Status:** **accepted** — workspace owner ruled 2026-06-05 to build the custom IBKR-REST
  connector (REST is a hard course requirement; Nautilus's IBKR adapter is TWS-only). The
  resolution below (§"Proposed decision") stands as accepted; the catalog seam it depends on
  landed in C1 (ADR 0025). The connector is implemented in `packages/infra-ibkr` (`cp_rest_*`),
  normalizing into `RawMarketEvent` alongside the Nautilus-TWS path, REST preferred.
- **Date:** 2026-06-05
- **Relates to / sits under:** [[0023-nautilus-runtime-spine-and-library-leverage]] (the spine
  reversal that landed the same day — read it first), [[0008-live-ibkr-adapter]] (the
  hand-rolled `ib_async` session 0023 retires), [[0003-market-data-plane]] (the trust boundary
  no broker type may cross).

## Context

A REST connection to IBKR is a **hard course requirement**. This ADR records how that lands
*after* ADR 0023, which reversed the spine to **Nautilus** and set the direction "IBKR rides
Nautilus's shipped InteractiveBrokers adapter, retiring the hand-rolled `ib_async` session."

The tension this must resolve: **Nautilus's InteractiveBrokers adapter connects over the TWS
API to TWS / IB Gateway — not the Client Portal REST/Web API** (verified 2026-06-05 against the
Nautilus docs — TWS-socket only, no REST option; see "Open question" below). So the owner's
"IBKR rides Nautilus" direction and
the course's "REST connection" requirement pull opposite ways *for IBKR specifically*: taking
Nautilus's IBKR adapter as-is does **not** satisfy the REST requirement.

ADR 0023 already contains the reconciliation pattern. Decision 3 keeps Vincent's Saxo/Deribit
adapters precisely because "Nautilus ships nothing" for them — i.e. **a broker Nautilus does not
adequately cover gets a custom adapter that normalizes into the one catalog the engine replays.**
A REST requirement makes Nautilus's IBKR coverage "insufficient" in exactly that sense (it is
TWS-only), so IBKR-over-REST becomes the same case as Saxo/Deribit: a custom adapter feeding the
catalog, not the shipped Nautilus adapter. The board's "moot unless Nautilus's IBKR coverage
proves insufficient" caveat is the hook this ADR fills.

## Decision (accepted — owner ruled 2026-06-05; see Ruling below)

1. **IBKR has two ingestion paths, both normalizing into the Nautilus catalog as
   `RawMarketEvent`** (the seam C1 is choosing per ADR 0023): the **shipped Nautilus IBKR
   adapter** (TWS / Gateway) and a **custom IBKR REST adapter** (Client Portal, the
   Saxo/Deribit pattern — `httpx`/`websockets` into the catalog). Nothing IBKR- or REST-specific
   crosses into the analytics; both produce the same normalized events with `content_event_id` +
   `ProvenanceStamp` (ADR 0023 §4).

2. **The path is chosen by config; REST is preferred, Nautilus-TWS is the manual-flip fallback.**
   A `transport: rest | nautilus-tws` flag picks one at wiring. The course requirement is met by
   running REST; the TWS/Gateway path stays as a net you switch back to by config while REST
   isn't 100%. **No automatic failover** — a hot standby is blocked by the single-session-per-
   username collision (would need a second IBKR username), and the requirement doesn't ask for
   unattended failover.

3. **Auth is the self-service Client Portal Gateway, not OAuth.** The requirement is "use REST,"
   not "drop the local process." CP Gateway delivers a real REST/WebSocket connection on a retail
   account with no OAuth onboarding (which stays gated, out of scope). We own the session
   lifecycle the TWS path hid: `/tickle` ~every minute, expired/dropped session surfaced as the
   engine's disconnect/backoff signal.

4. **Equivalence is the acceptance bar.** A captured chain replayed through the REST path must
   reconstruct the *same normalized raw events* the TWS path produces — swapping ingestion must
   not move a single downstream byte. Read-only invariant holds (REST order endpoints never
   called); the SDK/transport is lazily imported so the gate runs broker-free; no secrets in git.

## Open question — RESOLVED (owner ruled 2026-06-05; see Ruling at the end of this section)

ADR 0023 is owner-set direction; this ADR proposes an **exception to it for IBKR**, justified by
an external requirement. That is the owner's call, not an agent's:

- **Conflict confirmed real (2026-06-05, verified against Nautilus docs).** Nautilus's IBKR
  adapter is **TWS-API socket only** — it connects to TWS or IB Gateway (`ibg_host`/`ibg_port`,
  ports 7497/7496 TWS, 4002/4001 Gateway, or a dockerized Gateway) and a running TWS/Gateway
  process is mandatory; there is **no Client Portal / Web / REST option**. So "configure Nautilus
  for REST" is not available — §1's custom REST adapter is the only way to satisfy the requirement
  while keeping Nautilus as the spine. Sources: NautilusTrader IB integration docs
  (`nautilustrader.io/docs/nightly/integrations/ib/`, `github.com/nautechsystems/nautilus_trader`
  `docs/integrations/ib.md`).
- **If real, confirm the resolution:** custom IBKR REST adapter into the catalog (this ADR), vs
  pushing back on the course requirement, vs running the REST adapter *outside* Nautilus as a
  standalone collector. The first keeps one runtime and matches the Saxo/Deribit precedent.

**Ruling (2026-06-05):** the owner chose the first option — build the custom IBKR-REST connector
into the catalog. This ADR is now **accepted** and the transport code is authorized; it landed in
`packages/infra-ibkr` after C1 fixed the catalog seam (ADR 0025).

## Consequences

- Preserves the user's intent (two config-selected IBKR paths, REST preferred, TWS as manual
  fallback) while staying inside ADR 0023's "custom adapter for what Nautilus doesn't cover"
  frame — no contradiction with the spine direction, only an IBKR-specific exception that 0023's
  own logic already permits.
- The session-collision conclusion from `tasks/ibkr-rest-api-evaluation.md` is unchanged: REST
  buys nothing there; the shared-login fix remains "second IBKR username," independent of this.
