#!/usr/bin/env python
"""Live IBKR smoke: AAPL -> option universe -> quotes -> persisted raw events.

The one path the seam tests cannot exercise — a real socket to a running Gateway/TWS.
It connects **read-only**, qualifies an underlying, expands a *bounded* slice of its
option chain into the canonical universe, subscribes the underlying plus the option
conIds, collects for a fixed window, and prints what landed. It places no orders: the
session is read-only and no order endpoint is ever called.

Requires the optional broker SDK (it is imported lazily inside the adapter):

    cd backend && uv sync --extra ibkr

Then, against a running paper Gateway (default port 4002):

    uv run python scripts/ibkr_live_smoke.py --symbol AAPL --max-expiries 2 --seconds 30

Exit code 0 means: connected, stock qualified, options qualified, universe
materialized, conIds subscribed, and at least one raw event written. Non-zero means a
step failed (the failure is printed); fix it before relying on collection.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _BACKEND_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL", help="underlying symbol to resolve")
    parser.add_argument("--host", default="127.0.0.1", help="Gateway/TWS host")
    parser.add_argument("--port", type=int, default=4002, help="Gateway/TWS API port")
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="how long to collect before ending the stream cleanly")
    parser.add_argument("--max-expiries", type=int, default=2,
                        help="number of nearest expiries to qualify")
    parser.add_argument("--strike-window-pct", type=float, default=0.35,
                        help="keep strikes within +/- this fraction of spot")
    parser.add_argument("--min-strikes-per-side", type=int, default=10,
                        help="always keep at least this many strikes each side of spot")
    parser.add_argument("--market-data-type", type=int, default=3,
                        help="IBKR market-data type: 1 live, 2 frozen, 3 delayed, 4 delayed-frozen")
    parser.add_argument("--data-root", default=None,
                        help="store root (default: a throwaway temp dir)")
    parser.add_argument("--trade-date", default=None,
                        help="trade date YYYY-MM-DD (default: today)")
    return parser.parse_args(argv)


def _log(message: str) -> None:
    print(f"[ibkr-smoke] {message}", flush=True)


def run(args: argparse.Namespace) -> int:
    from collectors import MarketDataCollector
    from connectivity import (
        IbkrBrokerSession,
        SessionSupervisor,
        SystemClock,
        client_id_for,
    )
    from storage import ParquetStore
    from universe import ChainSelection, UniverseService, materialize_universe

    trade_date = (
        datetime.strptime(args.trade_date, "%Y-%m-%d").date()
        if args.trade_date
        else date.today()
    )
    data_root = Path(args.data_root or tempfile.mkdtemp(prefix="ibkr-smoke-"))
    store = ParquetStore(data_root)
    clock = SystemClock()

    selection = ChainSelection(
        max_expiries=args.max_expiries,
        strike_window_pct=args.strike_window_pct,
        min_strikes_per_side=args.min_strikes_per_side,
    )
    session = IbkrBrokerSession(
        host=args.host,
        port=args.port,
        readonly=True,
        market_data_type=args.market_data_type,
        max_runtime_seconds=args.seconds,
        selection=selection,
    )
    supervisor = SessionSupervisor(
        session, client_id=client_id_for("smoke"), clock=clock
    )

    _log(f"connecting read-only to {args.host}:{args.port} ...")
    supervisor.connect()
    if not supervisor.is_healthy():
        _log("FAIL: session did not report connected")
        return 1
    _log("connected (read-only; no order endpoint is called)")

    _log(f"resolving option universe for {args.symbol} ...")
    rows = supervisor.request_option_chain(args.symbol)
    if not rows:
        _log(f"FAIL: {args.symbol} did not qualify (no rows)")
        return 1
    stock_rows = [r for r in rows if r.get("secType") == "STK"]
    option_rows = [r for r in rows if r.get("secType") == "OPT"]
    if not stock_rows:
        _log("FAIL: no underlying row in the resolved universe")
        return 1
    if not option_rows:
        _log("FAIL: underlying qualified but no option contracts came back")
        return 1
    _log(f"qualified 1 underlying + {len(option_rows)} option contracts")

    materialize_universe(store, rows, trade_date)
    universe = UniverseService.load_active_universe(store, trade_date)
    underlying = universe.get_underlying(args.symbol)
    chain = universe.get_option_chain(args.symbol, trade_date)
    con_ids = [underlying.broker_contract_id] + [opt.broker_contract_id for opt in chain]
    _log(f"universe materialized: {len(con_ids)} instruments for {trade_date}")

    session_id = f"smoke-{args.symbol}-{trade_date.isoformat()}"
    collector = MarketDataCollector(
        store=store, universe=universe, session_id=session_id,
        trade_date=trade_date, clock=clock,
    )
    _log(f"subscribing {len(con_ids)} conIds; collecting for {args.seconds:.0f}s ...")
    summary = collector.collect(supervisor, subscribe=con_ids)
    supervisor.disconnect()

    _log(
        f"done: {summary.event_count} events, coverage "
        f"{summary.coverage_ratio:.2f}, gaps {summary.gap_count}"
    )
    if summary.event_count == 0:
        _log("FAIL: no raw events written (market closed, no entitlement, or thin feed)")
        return 1
    _log(f"OK: raw events persisted under {data_root}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 - a smoke prints the failure, never tracebacks at the operator
        _log(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
