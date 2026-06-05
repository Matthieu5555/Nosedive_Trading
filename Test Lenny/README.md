# Test Lenny

Standalone volatility console for IBKR paper-trading data. It does not import or
modify any other project directory.

## What it does

- Data tab: indices/underlyings, option quotes, IV, Greeks, and an IV smile chart.
- Risk tab: portfolio Greeks, scenario stress grid, worst-case stress, and concentration.
- Orders tab: paper order ticket, preview, blocked transmission by default, and audit log.
- Local lineage: every snapshot/order event is appended to `runtime/test_lenny.sqlite`.

The app starts in deterministic demo mode. IBKR mode is optional and reads from a
running TWS or IB Gateway paper session.

## Run demo mode

```bash
cd "/srv/project/Test Lenny"
python3 app/server.py
```

Open:

```text
http://127.0.0.1:8765
```

## Use IBKR paper data

Do not put your IBKR login in this app. Log in manually to TWS or IB Gateway with
your paper-trading account, then enable the API:

- TWS paper default port: `7497`
- IB Gateway paper default port: `4002`
- API host: `127.0.0.1`
- Allow socket clients in TWS/Gateway API settings

Install the optional adapter dependency with `uv`, then run:

```bash
cd "/srv/project/Test Lenny"
uv add ib-insync
IBKR_MODE=ibkr IBKR_HOST=127.0.0.1 IBKR_PORT=7497 IBKR_CLIENT_ID=71 uv run python app/server.py
```

Order transmission is blocked unless `IBKR_ENABLE_ORDERS=true`, and the current
IBKR order hook still refuses to transmit until contract qualification is completed.
That is deliberate: this first version is a visual analytics and paper-risk console,
not an execution engine.

## Verify

```bash
cd "/srv/project/Test Lenny"
uv run python -m unittest discover -s tests
uv run python -m py_compile app/*.py
```

## Design notes

The implementation follows the roadmap principles:

- raw event append-only storage in SQLite;
- explicit provenance with code version and config hash;
- deterministic demo mode for replayable UI development;
- no hidden credentials;
- orders separated from data/risk, with transmission disabled by default;
- pure analytics functions for pricing, Greeks, surface points, and scenario risk.
