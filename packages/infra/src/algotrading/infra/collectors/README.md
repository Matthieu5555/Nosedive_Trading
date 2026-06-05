# infra.collectors

Owner: **M4 — market-data plane / actor spine**.

⚠️ **Direction set by ADR 0023 (2026-06-05): keep this slice — it's the survivor.** Holds the
vendored EAV market-data slice the broker leaves use: `normalize.py` (the EAV `BrokerTick` +
`normalize_event`) and `collector.py` (`FeedFault`, the `MarketDataAdapter` protocol, the batching
`RawCollector`). Under ADR 0023 Saxo/Deribit keep this `MarketDataAdapter` path and IBKR comes via
Nautilus; all three normalize to one `RawMarketEvent` in the catalog Nautilus replays. C1 finishes
it: restore **content-addressed** event ids (the current `evt-{n}` running counter isn't
reconnect-stable), add the replay/config/summary pieces, and retire the scalar
`contracts.BrokerSession`. History in ADR 0020/0022.
