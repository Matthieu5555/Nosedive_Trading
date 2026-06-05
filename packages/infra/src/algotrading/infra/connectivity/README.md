# infra.connectivity

Owner: **M4 — market-data plane / actor spine**.

⚠️ **Partially filled by M5 ahead of M4 (ADR 0022, which contests ADR 0020).** Currently holds a
vendored *minimal slice*: `session.py` — the `BrokerTransport` protocol plus the connection
*lifecycle* state machine (`BrokerSession`, reconnect/backoff, heartbeat). NB this
`connectivity.session.BrokerSession` is the connection lifecycle *class*, distinct from the
M0-frozen `contracts.broker.BrokerSession` *protocol* the leaves are meant to implement at the data
seam — reconciling the two is M4's job per ADR 0020. This slice collides with M4's version on
relocation (a deliberate, visible merge conflict); M4 owns the survivor. See ADR 0022.
