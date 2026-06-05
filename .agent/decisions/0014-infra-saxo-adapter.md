# 0014 — `infra-saxo`: Saxo Bank OpenAPI adapter, OAuth2, equity-first

- **Status:** accepted
- **Date:** 2026-06-02
- **Source:** Vincent's ADR-017; merged 2026-06-05

## Context

The IBKR KYC was blocked. A live, funded Saxo Bank account was available immediately. Getting a
real live broker wired before IBKR unblocks validates the adapter seam and the OAuth flow without
waiting. Saxo also has architectural advantages for option surface collection: the `OptionsChain`
REST endpoint returns a full IV matrix and Greeks in a single call (vs. IBKR's per-contract
subscription model).

The off-the-shelf `saxo-openapi` PyPI package is frozen at v0.6.0 (last updated 2019) and does
not support Python 3.13. Direct `httpx` calls are simpler and have no extra dependencies.

## Decision

1. **`packages/infra-saxo/` is a leaf package under ADR 0012.** Same structural position as
   `infra-deribit`: consumes `infra` protocols, never imported by `strategy`.

2. **Auth: OAuth2 Authorization Code flow.** Access token valid 20 minutes, refresh 40 minutes;
   rotation runs on a background thread in `auth/token_manager.py`. A certificate-based flow is
   deferred until a production need arises.

3. **Transport: `httpx` (REST) + `websockets` (streaming).** No `saxo-openapi` dependency.
   Token injection is a callable (`token_fn`) injected into the transport, keeping auth separate
   from connectivity.

4. **Discovery: 4-step** — authenticate, list underlyings, fetch option chain, map to
   `OptionContract`. This step is pure and testable without a live network.

5. **Known constraints (amber):**
   - SPX options are excluded at the Saxo API level (CBOE exchange agreement) — not a Saxo limitation.
   - SPY chains may span > 100 strikes per session; PATCH-based pagination is required for a complete
     surface.
   - WebSocket payload fields are partially reconstructed from non-public documentation; validate
     exact field names with a live token before relying on them in production.

6. **Dependency: `httpx` + `websockets` only.** `saxo-openapi` is not used.

## Consequences

Live surface collection is unblocked without IBKR. The OAuth flow and 4-step discovery pattern
documented here are reusable as the template for any REST+OAuth broker adapter. IBKR remains the
long-term target; Saxo provides immediate live validation.
