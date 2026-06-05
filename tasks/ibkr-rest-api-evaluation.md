# IBKR REST API — evaluate replacing the TWS-API/Gateway transport

- **Branch:** `feat/ibkr-rest-api` (spike first; only widen to a migration if the spike clears the blocker below)
- **Owns:** `backend/src/connectivity/**` (today's `ibkr_session.py`), and in the merged repo `packages/infra-ibkr/**`.
- **Depends on:** M0 (`BrokerSession` protocol — the seam any new transport must satisfy), M4 (adapter-to-actor wiring).
- **Relates to:** [M5-broker-adapters.md](M5-broker-adapters.md) — this is a **fourth option** in the IBKR bake-off, which today weighs only TWS-API transports (`ib_async`, Vincent's `ibkr_transport`, Nautilus's built-in). ADR [`0008-live-ibkr-adapter`](../.agent/decisions/0008-live-ibkr-adapter.md) records why the current adapter is TWS-API-over-`ib_async`.

## Update 2026-06-05 — two things changed the same day; read both

This doc's disposition was written when REST was an *optional* operational migration over the
hand-rolled `ib_async` adapter. **Two things have since changed and they interact:**

1. **REST is now a hard course requirement**, not an optional spike.
2. **ADR [0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md) landed**
   and reversed the spine to **Nautilus**, with direction "IBKR rides Nautilus's shipped adapter,
   retiring the `ib_async` session this doc evaluates."

These pull opposite ways for IBKR: **Nautilus's IBKR adapter is TWS-API/Gateway, not REST**
(verified 2026-06-05 against the Nautilus docs — TWS-socket only, no Client Portal/REST option),
so adopting it as-is does *not* meet the course requirement. The proposed
reconciliation is in **ADR [0024](../.agent/decisions/0024-ibkr-rest-transport-alongside-tws.md)
(status: proposed)** — treat IBKR-over-REST as the same case as Saxo/Deribit (a custom adapter
normalizing into the Nautilus catalog, because Nautilus's coverage is "insufficient" for the REST
requirement), with the Nautilus-TWS path as a config-flip manual fallback. **No automatic
failover; no second username; CP-Gateway auth, not OAuth.** That ADR is *proposed*, not accepted —
it asks the workspace owner whether IBKR gets this exception to ADR 0023, and it is sequenced
after C1 owns the catalog seam.

What still holds from the research below, regardless of the above: the **session-collision**
conclusion (REST buys nothing; the shared-login fix is a second IBKR username) and the **OAuth
gating** facts. What is now *stale*: any framing of this as a `BrokerSession`-seam transport swap
or a Gateway *replacement* — under ADR 0023 the seam is the catalog, and TWS-via-Nautilus stays as
the fallback rather than being removed.

Everything below is the original research, kept verbatim as the evidence base.

---

## Why this exists

Today we talk to IBKR over the **TWS API** (binary socket protocol) via a local **IB Gateway** — `ib_async` → Gateway (`:4002` paper / `:4001` live) → IBKR. That means a always-on Java Gateway process holding a logged-in session. The question raised: should we move to IBKR's **REST API** instead?

The trigger was the single-session collision on a shared login (the running Gateway holds the one allowed session, so a second user is refused). **That motivation has now been researched (2026-06-05) and resolves negative — see the blocker below.** REST does *not* escape the collision for our use case; the real fix stays "separate usernames."

The honest upside of REST is therefore **operational, not session-related**: the OAuth Web API flavor removes the local Gateway/IBC process to supervise (the thing we had to hunt down and kill), simpler deployment/containerization, HTTP/WebSocket instead of a binary socket. The honest cost is a full transport rewrite, a new keepalive/auth lifecycle (`/tickle`, 5-min session timeout), and re-validation of the whole `BrokerTick` mapping and chain expansion against a different wire shape.

## Decision blocker — RESEARCHED 2026-06-05, resolves NEGATIVE for the session motivation

**Question:** Does IBKR's REST API give a session that does *not* collide with TWS/Gateway sessions for the same user?

**Answer: No — not for a market-data collector.** IBKR's Web API has a two-tier session model (verified against IBKR Campus / Client Portal docs, sources at bottom):

- An **outer read-only session** (portfolio / account-management queries) *can* coexist with a TWS/Gateway login. **But it carries no market data and no `/iserver` access.**
- A **brokerage session** is required for *all `/iserver` endpoints — which is where both market data AND the `secdef` option-chain endpoints live* — and is **single-per-username across all IBKR services**. IBKR's docs: *"if you are logged into TWS or Client Portal, you must log out before reauthenticating your Client Portal API session."*

Our adapter is read-only only in the *"places no orders"* sense; it fundamentally needs market data, so it needs a brokerage session, so it collides with TWS exactly like today. **REST buys us nothing on the shared-login problem.** IBKR's own recommended remedy is the one already on the table: *"create a new username for other services."*

**OAuth availability (also researched):** the Client Portal / Web API supports **IBKR Pro accounts only**; OAuth 2.0 is institutional/enterprise; third-party developers can currently obtain only **OAuth 1.0a** approval, behind an onboarding / consumer-key request — there is no self-service OAuth for a retail individual. So the one REST flavor that would drop the local gateway is gated.

**Conclusion / how this task proceeds:**
- The shared-login collision is **closed as "add a second IBKR username"** (or paper users) — *not* a transport change. This is now reflected back in the [M5 IBKR bake-off](M5-broker-adapters.md).
- The task does **not** die outright: there is still a standalone **operational** case for the OAuth Web API (no Gateway/IBC to run/supervise/auto-die). That is the *only* remaining reason to pursue it, and it is **optional, low priority, and contingent on OAuth 1.0a onboarding succeeding for this account**. Everything in "Scope" below applies only to that operational migration.

## Scope (operational migration only — pursue only if dropping the Gateway is worth the OAuth onboarding)

Re-implement the IBKR `BrokerSession` over REST while keeping the seam byte-for-byte unchanged downstream. Concretely, the new transport must still:

- Emit the same broker-agnostic `BrokerTick`s (field-name strings, the `-1` no-value drop, size-vs-price fields) the actor/collectors already consume — the REST market-data tick shape replaces the IB tick-type integers, but **nothing IBKR-specific may cross the seam** (ADR 0003/0008).
- Stream quotes (REST market-data is WebSocket for live ticks + REST for snapshots — map both into the existing tick queue / `ticks()` iterator and the supervisor's `SessionDisconnected` drop signal).
- Resolve a symbol into the same `conId`-keyed universe rows (`request_option_chain` shape): underlying + bounded option chain. The REST chain discovery is a **mandatory sequential** call chain — `/iserver/secdef/search` → `/iserver/secdef/strikes` → `/iserver/secdef/info` — with a documented gotcha: **sending the `name` field on `/search` suppresses `/strikes`**, so omit it when building a chain. Map this onto today's `qualifyContracts` / `reqSecDefOptParams` flow and preserve `ChainSelection` (spot-windowed strikes, nearest `max_expiries`, median fallback). Live ticks arrive via WebSocket (`smd+CONID+{"fields":[...]}`); snapshots via `/iserver/marketdata/snapshot` — the field tags are shared between the two.
- Own the **session lifecycle the TWS API hid from us**: authenticate the brokerage session, then **`/tickle` roughly every minute** — the brokerage session times out after ~5–6 minutes of silence. A long-running collector must keep-alive or it silently dies; surface a dropped/expired session as the supervisor's `SessionDisconnected` so backoff-reconnect still owns recovery.
- Stay **read-only / places no orders** — the platform-wide invariant. REST exposes order endpoints; the adapter must never call them.
- Keep the SDK/transport optional and lazily imported, so the package and the gate still run broker-free (the disk replay + seam tests must not need a live REST session).

## Test surface

Read [TESTING.md](TESTING.md) first. Specific to this task:
- A **fake REST/WebSocket transport** drives the full adapter → `BrokerSession` → M4 plane with no live socket in the suite (the live-broker ban stands; a live REST session proves out via a smoke script, not pytest — mirror `scripts/ibkr_live_smoke.py`).
- **Equivalence:** the same captured chain must reconstruct into the *same normalized raw events* the TWS-API adapter produces (carry/extend the real-sample reconstruct test). This is the headline assertion — swapping transports must not move a single downstream byte.
- Auth: token/keepalive/refresh and session-expiry paths tested against a fake auth server; **no real token or session cookie in git** (lives in `$HOME`/`.env`, per AGENTS.md).
- Read-only proof: an order endpoint is never called (assert against the fake transport).

## Done criteria

The session-collision motivation is already resolved (negative) above; the **minimum** close for that part is a short ADR + a `known-limitations` note recording *why we stay on TWS API for now* and that the shared-login fix is a second IBKR username. That can land without writing any transport code.

The **optional** operational migration is done only when `infra-ibkr` ships a REST `BrokerSession` that drives M4's actor **identically** to the TWS-API one — equivalence test green, the `/tickle` keepalive + session-expiry lifecycle handled and surfaced as `SessionDisconnected`, read-only proven, no secrets in git, gate green — and a new ADR supersedes the relevant parts of 0008. Do not start this half unless OAuth 1.0a onboarding for this account has actually been granted (otherwise the local Client Portal Gateway is still required and the "no Gateway process" upside evaporates).

## Gotchas

- **REST does not fix the session collision for a market-data collector** (researched — the brokerage session is single-per-username; the read-only session that coexists carries no market data). The only reason left to do the migration is dropping the local Gateway via OAuth — don't conflate the two.
- **OAuth is gated:** Client Portal/Web API is IBKR-Pro-only; retail individuals get OAuth 1.0a via an onboarding/consumer-key approval, not self-service. Confirm it's granted before committing to the no-gateway design.
- **Session keepalive is now our job:** `/tickle` ~every minute or the brokerage session dies in ~5 min. The TWS API/Gateway hid this; the REST adapter must own it.
- No secrets in git — REST auth tokens/session cookies/consumer keys are the trap.
- Keep the rewrite *below* the seam: the actor, collectors, universe resolver, and replay must not change. If something REST-specific wants to leak upward, the seam is wrong, not the seam's fault.
- Live sockets/sessions are never in the test suite.

## References (researched 2026-06-05)

- [Client Portal Web API v1.0 — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/) — session model, `/tickle`, endpoints.
- [Web API Documentation — IBKR Campus](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/) — OAuth 1.0a/2.0 flavors and account-type availability.
- [Authenticating with the IBKR Client Portal REST API — IBKR Campus](https://www.interactivebrokers.com/campus/traders-insight/authenticating-with-the-ibkr-client-portal-rest-api/) — brokerage vs read-only session, single-session-per-username.
- [Handling Options Chains — IBKR Quant](https://www.interactivebrokers.com/campus/ibkr-quant-news/handling-options-chains/) — the mandatory `secdef/search → strikes → info` sequence and the `name`-field gotcha.
- [Client Portal API docs (GitHub mirror)](https://interactivebrokers.github.io/cpwebapi/) — WebSocket `smd+CONID` market-data subscription, `/iserver/marketdata/snapshot`.
