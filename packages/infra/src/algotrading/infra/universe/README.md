# infra.universe

Owner: **M4 — market-data plane / actor spine**.

⚠️ **Partially filled by M5 ahead of M4 (ADR 0022, which contests ADR 0020).** Currently holds a
vendored *minimal slice*: `contracts.py` (canonical `Underlying`/`OptionContract` + the reversible
`instrument_key`), `discovery.py` (`OptionParams` → canonical contracts), and `master.py` (only
`UniverseError`). The full queryable master (`InstrumentUniverse`/`build_universe`/
`MonitoredUniverse`) and the broker-neutral `chain_planning`/`AvailableChain` selection policy that
ADR 0020 specifies are **not** here yet — they land with M4. This slice collides with M4's version
on relocation (a deliberate, visible merge conflict); M4 owns the survivor. See ADR 0022.
