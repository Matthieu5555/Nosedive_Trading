# REP7 — Collapse hand-rolled connectivity into the Nautilus IBKR adapter

> **BLOCKED — do not start until a live `TradingNode` is stood up.**
> ([AUDIT-library-leverage-2026-06-07.md](AUDIT-library-leverage-2026-06-07.md))
> ~430–510 LOC of duplicated reconnect/heartbeat/clock that the Nautilus IBKR adapter +
> `LiveClock` already own. [ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md)
> §3 already marks the `ib_async`-era session for retirement.

- **Owns:** `packages/infra/src/algotrading/infra/connectivity/` — `session.py` (288),
  `supervisor.py` (240), `clock.py` (72); the live wiring around
  `packages/infra-ibkr/src/algotrading/infra_ibkr/connectivity/nautilus_ibkr.py`
  (`build_data_client_config` already exists).
- **Depends on:** a **live `TradingNode`** being wired (today only Nautilus's *backtest*
  engine drives the system; capture 1C uses replay, not a live node). This is the hard
  precondition — it belongs to the live-trading workstream, not yet specced.
- **Blocks:** nothing; it's a cleanup that rides on live trading landing.
- **State going in:** two full reconnect state machines (`session.py` + `supervisor.py`,
  overlapping) plus a parallel `clock.py`, none of which Nautilus drives. The IBKR adapter +
  `LiveClock`/`TestClock` already own connect/reconnect/heartbeat/re-subscribe.

## Objective

Once a live node exists, delete the duplicated connectivity and let the Nautilus IBKR adapter
own the IBKR lifecycle — **without** touching the bespoke capture invariants.

## What to do (ordered) — only after the live node is wired

1. **Wire the live `TradingNode`** via the existing `build_data_client_config`
   (`nautilus_ibkr.py`) — this is the precondition, likely its own task.
2. **Retire `connectivity/session.py`** (the `ib_async`-era seam ADR 0023 §3 flags).
3. **Fold `connectivity/supervisor.py`** reconnect/heartbeat/re-subscribe into the adapter.
   **Keep** the IBKR client-id band and the gap → `GapInterval` → collector seam — loss-aware
   gap records are load-bearing. (ADR 0023 §3's "Saxo/Deribit need their own lifecycle" is void:
   both were removed in T-index-only-refactor; IBKR is the sole live broker.)
4. **Drop `connectivity/clock.py`** for Nautilus `LiveClock` / `TestClock`. Do this *with*
   steps 2–3 — a standalone clock swap is low-value churn.
5. **Do NOT touch** the content-addressed dedup / immutability in `collectors/collector.py`
   (ADR 0027) — that is bespoke on purpose and not Nautilus's job. The
   raw-store-as-`ParquetDataCatalog` question (audit S5/S6) is a **separate open design
   decision** (ADR 0023 catalog-topology), not part of this task.

## Done when

A live `TradingNode` runs IBKR market data; `session.py` + `clock.py` are gone and
`supervisor.py`'s IBKR duties are in the adapter; the gap-recording seam + capture dedup are
intact; root gate + the headline acceptance tests (replay/provenance/
reconstruction/handover) green.
