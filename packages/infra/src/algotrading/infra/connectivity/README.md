# infra.connectivity

Owner: **M4 — market-data plane / actor spine**.

⚠️ **Direction reset by ADR 0023 (2026-06-05): Nautilus is the runtime spine.** Currently holds a
vendored *minimal slice*: `session.py` — the `BrokerTransport` protocol plus the connection
*lifecycle* state machine (`BrokerSession`, reconnect/backoff, heartbeat). Note this
`connectivity.session.BrokerSession` is the connection lifecycle *class*, distinct from the
M0-frozen `contracts.broker.BrokerSession` *protocol*; under ADR 0023 the latter (the scalar pull
seam) is being **retired** in favour of normalizing every broker into `RawMarketEvent` in the
catalog Nautilus replays. C1 owns that reconciliation (IBKR → Nautilus's adapter; Saxo/Deribit keep
their `MarketDataAdapter`). See ADR 0023; history in ADR 0020/0022.
