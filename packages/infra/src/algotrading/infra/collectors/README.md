# infra.collectors

Owner: **M4 — market-data plane / actor spine**.

⚠️ **Partially filled by M5 ahead of M4 (ADR 0022, which contests ADR 0020).** Currently holds a
vendored *minimal slice* the broker leaves need: `normalize.py` (the EAV `BrokerTick` +
`normalize_event`) and `collector.py` (`FeedFault`, the `MarketDataAdapter` protocol, the batching
`RawCollector`). The replay source, collector config, and session-summary tooling are **not** here
yet — they land when M4 relocates its plane from `backend/src`. When it does, this slice collides
with M4's version (a deliberate, visible merge conflict); M4 owns the survivor. See ADR 0022.
