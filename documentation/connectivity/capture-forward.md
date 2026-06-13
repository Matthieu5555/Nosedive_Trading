# Capture-forward (scheduled)

The free-data path is **capture-forward**: capture each market day live, then reconstruct it offline.
Both CLIs are **idempotent** and **partitioned by `trade_date`**, so re-running a day overwrites
nothing it shouldn't and a missed day is simply backfilled by re-running. No Python scheduler module
is needed — a plain OS scheduler invoking the two CLIs is enough.

> **Ported from the pre-merge reference tree (2026-06-05).** The capture/reconstruct CLIs referenced
> here (`saxo_sustained_capture.py`, `saxo_reconstruct.py`, `deribit_collector_run.py`,
> `deribit_reconstruct.py`, `saxo_oauth.py`) lived in the pre-merge `scripts/` directory and have
> **not yet been relocated** into the canonical monorepo tree. The command lines below describe the
> intended workflow; until the connector scripts are ported, the idempotent-capture / partition-by-day
> contract they rely on is what is load-bearing here.

## The two CLIs

| Step | Saxo (equity) | Deribit (crypto) |
|---|---|---|
| Capture → raw store | `scripts/saxo_sustained_capture.py --symbol ASML --minutes 60 --n-expiries 1 --env live` | `scripts/deribit_collector_run.py --seconds 60 --min-days 10 --max-days 45` |
| Reconstruct → surface | `scripts/saxo_reconstruct.py --symbol ASML` | `scripts/deribit_reconstruct.py --currency BTC` |

Run capture **during the underlying's market hours**; reconstruct any time after (it reads the stored
raw partition for the day). Saxo needs a valid token — refresh it once with
`scripts/saxo_oauth.py --env live` before the scheduled window (the capture refreshes in-session
thereafter).

## Linux / macOS — cron

```cron
# Capture ASML during Euronext hours (07:05 UTC), reconstruct after close (15:45 UTC), weekdays.
5  7  * * 1-5  cd /path/to/AlgoTrading && uv run python scripts/saxo_sustained_capture.py --symbol ASML --minutes 480 --n-expiries 1 --env live >> logs/saxo_capture.log 2>&1
45 15 * * 1-5  cd /path/to/AlgoTrading && uv run python scripts/saxo_reconstruct.py --symbol ASML >> logs/saxo_reconstruct.log 2>&1
```

## Windows — Task Scheduler

Two scheduled tasks (Task Scheduler → Create Task → Triggers: daily, weekdays; Action: Start a program):

```
Program/script:  powershell.exe
Arguments:       -Command "cd C:\path\to\AlgoTrading; uv run python scripts/saxo_sustained_capture.py --symbol ASML --minutes 480 --n-expiries 1 --env live"
```
```
Program/script:  powershell.exe
Arguments:       -Command "cd C:\path\to\AlgoTrading; uv run python scripts/saxo_reconstruct.py --symbol ASML"
```

Or register from a shell with `schtasks`:

```powershell
schtasks /Create /TN "AlgoTrading-SaxoCapture" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 09:05 `
  /TR "powershell -Command \"cd C:\path\to\AlgoTrading; uv run python scripts/saxo_sustained_capture.py --symbol ASML --minutes 480 --n-expiries 1 --env live\""
```

## Linux server — systemd timer (the daily index close-capture, WS 1G)

The index options-analytics pipeline's daily close-capture runs unattended via a **systemd timer
+ `oneshot` service**, not a cron line and not an in-process scheduler ([ADR 0032](../../.agent/decisions/0032-unattended-scheduling-via-systemd-timers.md)).
The timer is the scheduler; the runner (`scripts/eod_run.py` → `algotrading.infra.orchestration.eod_runner`)
stays a one-shot. Committed units live next to this guide:

| Unit | Role |
|---|---|
| `eod-capture@.service` | the `Type=oneshot` template that runs `uv run python scripts/eod_run.py --calendar %i`; `Restart=on-failure` + `RestartSec=` retry; `OnFailure=eod-capture-alert.service` |
| `eod-capture@XEUR.timer` / `eod-capture@XNYS.timer` | one per **exchange calendar** — `OnCalendar=` shortly after that exchange's close, **timezone stated explicitly** (`Europe/Berlin` for Eurex, `America/New_York` for NYSE); `Persistent=true` for missed-run catch-up |
| `eod-capture-alert.service` | the minimal `OnFailure=` target — one journald notification per failed run |

Install (per-user, no root):

```bash
loginctl enable-linger "$USER"                         # user timers fire while logged out
mkdir -p ~/.config/systemd/user
cp documentation/connectivity/eod-capture@.service       ~/.config/systemd/user/
cp documentation/connectivity/eod-capture-alert.service  ~/.config/systemd/user/
cp documentation/connectivity/eod-capture@*.timer        ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now eod-capture@XEUR.timer eod-capture@XNYS.timer
```

Operate / query the run history (journald + the run-state ledger share one `correlation_id` per fire):

```bash
systemctl --user list-timers 'eod-capture@*'           # next fire per calendar
journalctl --user -u eod-capture@XEUR.service --since today   # the run trace + correlation_id
uv run python scripts/eod_run.py --index SX5E --trade-date 2026-06-05   # manual catch-up / backfill
```

Why this and not more: the fixed `OnCalendar` time is only a **safe upper bound trigger** after the
regular close; the runner resolves the *exact* close instant (and skips holidays/half-days) from the
1J exchange-calendar (`session_close`/`is_session`), so a holiday is a clean no-op and a half-day
resolves to its early close — no timer edit needed. **Adding an index on an already-covered calendar
needs no new unit** (the runner reads `enabled_indices()`); a brand-new exchange calendar adds one
timer. **Graduation trigger** (ADR 0032): move off the timer to an orchestration platform (Prefect /
Dagster) only when this stops being one independent daily job and becomes a DAG of interdependent
tasks/backfills needing a shared run UI — until that DAG materialises, a timer is correct.

Until WS 1C closes the broker→raw-event collection seam in production, the runner's default stage
wiring raises a labeled error; the timer path is fully exercised today through the injected
replay/fixture wiring in `packages/infra/tests/test_eod_run.py`. Swapping the collection stage to
`collect_live` is the one edit 1C makes — the runner, the manifest freeze, and the timer are already
correct.

## Notes

- **Never commit captured data** — `data/` is gitignored (non-redistribution). For offline/remote
  reproduction tests, use the committed **real delayed-quote sample slices**
  (`packages/infra-{saxo,ibkr}/samples/`, e.g. `asml_real_2026-06-04.json`), which reconstruct to a
  surface with no broker connection via `scripts/reconstruct_sample.py` (the curated last-tick slices
  are written by `scripts/export_sample.py`). The pre-merge tree also kept synthetic golden fixtures
  under `packages/infra/tests/golden/`; those are exercised by the acceptance tests, not by these CLIs.
- **Backtest depth** (long historical ranges) is a separate concern — capture-forward accumulates the
  history first. A historical-data provider would plug in as another event source — currently **YAGNI**
  under the Nautilus runtime spine ([ADR 0023](../../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md)) —
  Nautilus's data catalog + replay engine is the backtest path. Either way it slots in with no
  pipeline change.

_Ported & re-pointed 2026-06-05 from the pre-merge reference tree._
