# ibkr-broker-account-read — the CP-REST account read-path (positions / cash / fills) reconciliation needs

> **State (2026-06-15, branch `ibkr-broker-account-read`):** DONE — read-only CP-REST account
> collector built. Frozen-seam contracts `BrokerPosition` / `BrokerCashBalance` / `BrokerFill` +
> the in-memory `BrokerAccountSnapshot` bundle (`infra/contracts`, additively registered as
> `broker_positions` / `broker_cash_balances` / `broker_fills`); the broker leaf collector
> `infra-ibkr/collectors/{cp_rest_account_wire,cp_rest_account}.py` (read-only `/portfolio/*` +
> `/iserver/account/trades` GETs, order endpoints asserted never called; malformed rows rejected
> at the door; fills stamped at their own venue time — no look-ahead). Gate green
> (ruff + mypy + lint-imports clean, 2110 passed / 12 skipped). The SHARED SEAM for the recon
> sub-lane of `execution-operational-hardening` is the three `Broker*` contracts — clean and
> additive. Live bring-up is a smoke run, not pytest.

> **Source:** TARGET §5.9 + §6 ("broker position/cash/fill reconciliation") + R4 (ADR 0042/0024 —
> CP-REST is *the* IBKR path). **Found by the 2026-06-14 IBKR-coverage audit:** reconciliation is
> specced as sub-lane 3 of [[execution-operational-hardening]], but the IBKR-side capability it
> reads from does not exist and no `ibkr-` task owns it.

## The gap

The broker leaf (`packages/infra-ibkr`) exposes **market-data ingestion only** — snapshot,
discovery, history. Verified: there is **no** call to any account/portfolio endpoint
(`/portfolio/{accountId}/positions`, `/portfolio/{accountId}/ledger`, `/iserver/account`,
`/iserver/account/orders`/trades) anywhere in the leaf. Reconciliation (§6 — broker cash/position/
fill vs the internal book) cannot run without that read, so the recon sub-lane assumes a data
source that isn't built.

## Scope (this leaf, read-only)

- Add a **read-only** CP-REST account collector: positions, cash/ledger balances, and the
  day's fills/trades, normalized into typed contracts the recon layer consumes. Reuse the
  existing transport + session machinery (`cp_rest_transport.py`, `cp_rest_session.py`,
  OAuth/cookie selection) — no new transport.
- Stay strictly read: only `/portfolio/*` and `/iserver/account/*` GETs; **never** an order
  endpoint. Mirror the adapter's read-only assertion test (`test_cp_rest_adapter.py`).
- Typed wire shapes in the `cp_rest_wire.py` pydantic pattern; normalize at the door
  (reject malformed, no look-ahead — fills stamped at their own venue time).

## Out of scope / boundaries

- The reconciliation **logic** (broker-vs-book diff, tolerances, alerting) is
  `execution-operational-hardening` sub-lane 3 — this task **feeds** it the broker side only.
- Order submit (3A/3B) — separate gated seam ([[execution-order-sign-and-send]]); not here.
- No Saxo/Deribit (ADR 0042); REST/OAuth only (R4).

## Depends on / blocks

- Independent of the analytics/strategy lanes; builds on the landed CP-REST transport/session.
- **Blocks** the broker-reconciliation sub-lane of [[execution-operational-hardening]] and any
  non-paper booking (recon gates live booking per that task).

## Done criteria

A read-only CP-REST collector banks the account's current positions, cash/ledger, and the day's
fills into typed contracts; only account/portfolio GETs are touched (order endpoints asserted
never called); malformed rows rejected at the normalize door; gate green against fakes (the
broker-free CI seam unchanged; live bring-up is a smoke run, not pytest).
