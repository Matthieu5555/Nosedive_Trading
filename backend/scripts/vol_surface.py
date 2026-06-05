#!/usr/bin/env python
"""Pull a live option chain off the Gateway and print its fitted volatility surface.

The operator-facing companion to ``ibkr_live_smoke.py``: where the smoke proves the
*socket* works, this proves the *analytics* work end to end on real quotes. It is a thin
client over :func:`orchestration.build_surface` — it parses arguments, constructs the
injected dependencies (a read-only IBKR session, a store, the shipped config), runs the
job, and renders the result. All the workflow — chain planning, collection, the frozen
``actor.run_day`` pipeline, entitlement assessment, and the surface summary — lives in
reusable modules, not here, so a scheduled job or an API reaches a surface the same way.

It places no orders (the session is read-only and no order endpoint is ever called) and
adds no analytics of its own: every number printed comes out of the job's
:class:`~orchestration.SurfaceJobResult`, so what you see is byte-for-byte what a replay of
the same raw events would produce.

Requires the optional broker SDK (imported lazily inside the adapter):

    cd backend && uv sync --extra ibkr

Then, against a running paper Gateway (default port 4002), for the S&P 500 ETF:

    uv run python scripts/vol_surface.py --symbol SPY --max-expiries 4 --seconds 45

Notes for an index/ETF surface:

* SPY options are American-exercise. The surface itself does not depend on exercise style
  (the IV is quoted under the usual Black-76 forward convention, and no positions are
  priced here), so the printed smile is exercise-style-agnostic.
* The forward per maturity is recovered from put-call parity, so the index's dividend
  yield and the financing rate are implied from the quotes, not assumed.
* Delayed data (``--market-data-type 3``) is fine for the shape of the surface.

Exit code 0 means a surface was fitted for at least one maturity. Non-zero means a step
failed (printed) or no maturity had enough usable quotes to fit — widen the strike window
or the collection window and retry, or check the printed feed status for an entitlement
problem.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestration import SurfaceJobResult

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _BACKEND_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

_CONFIG_PATH = _BACKEND_ROOT.parent / "configs" / "default.toml"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY", help="underlying symbol (default SPY)")
    parser.add_argument("--host", default="127.0.0.1", help="Gateway/TWS host")
    parser.add_argument("--port", type=int, default=4002, help="Gateway/TWS API port")
    parser.add_argument("--seconds", type=float, default=45.0,
                        help="how long to collect quotes before fitting")
    parser.add_argument("--max-expiries", type=int, default=4,
                        help="number of nearest expiries to qualify (one smile each)")
    parser.add_argument("--strike-window-pct", type=float, default=0.15,
                        help="keep strikes within +/- this fraction of spot")
    parser.add_argument("--min-strikes-per-side", type=int, default=12,
                        help="always keep at least this many strikes each side of spot")
    parser.add_argument("--market-data-type", type=int, default=3,
                        help="IBKR market-data type: 1 live, 2 frozen, 3 delayed, 4 delayed-frozen")
    parser.add_argument("--data-root", default=None,
                        help="store root (default: a throwaway temp dir)")
    parser.add_argument("--trade-date", default=None,
                        help="trade date YYYY-MM-DD (default: today)")
    return parser.parse_args(argv)


def _log(message: str) -> None:
    print(f"[vol-surface] {message}", flush=True)


def _render(symbol: str, result: SurfaceJobResult) -> int:
    """Print the feed status and the fitted surface; return 0 if any SVI smile was fitted."""
    status = result.market_data_status
    summary = result.collection
    _log(
        f"collected {summary.event_count} events, coverage {summary.coverage_ratio:.2f}, "
        f"gaps {summary.gap_count}"
    )
    _log(f"feed: {status.describe()}")

    slices = result.slices
    if not slices:
        _log("no maturity had enough usable quotes to fit a smile.")
        if status.has_entitlement_problem:
            _log("the feed reports an entitlement problem — see the feed line above.")
        else:
            _log("try a wider --strike-window-pct or a longer --seconds.")
        return 1

    print()
    print(f"Volatility surface — {symbol}  (SVI per maturity, forward from put-call parity)")
    print(f"{'expiry':<12}{'T (yr)':>8}{'ATM vol':>10}{'pts':>6}{'rmse':>10}{'arb-free':>10}")
    print("-" * 56)
    for s in slices:
        print(
            f"{s.expiry_date.isoformat():<12}{s.maturity_years:>8.4f}"
            f"{s.atm_vol * 100:>9.2f}%{s.n_points:>6}{s.rmse:>10.2e}"
            f"{('yes' if s.arb_free else 'NO'):>10}"
        )
    print("-" * 56)
    print("ATM vol is sqrt(w(0)/T) read off each fitted SVI smile; arb-free is the")
    print("per-slice butterfly verdict. A 'NO' flags a butterfly arbitrage to inspect.")
    return 0


def run(args: argparse.Namespace) -> int:
    from config import config_hash, load_config
    from connectivity import (
        IbkrBrokerSession,
        SessionSupervisor,
        SystemClock,
        client_id_for,
    )
    from orchestration import SurfaceJobRequest, build_surface
    from storage import ParquetStore
    from universe import ChainSelection

    trade_date = (
        datetime.strptime(args.trade_date, "%Y-%m-%d").date()
        if args.trade_date
        else date.today()
    )
    data_root = Path(args.data_root or tempfile.mkdtemp(prefix="vol-surface-"))
    store = ParquetStore(data_root)
    config = load_config(_CONFIG_PATH)
    clock = SystemClock()

    selection = ChainSelection(
        max_expiries=args.max_expiries,
        strike_window_pct=args.strike_window_pct,
        min_strikes_per_side=args.min_strikes_per_side,
    )
    session = IbkrBrokerSession(
        host=args.host, port=args.port, readonly=True,
        market_data_type=args.market_data_type, max_runtime_seconds=args.seconds,
        selection=selection,
    )
    supervisor = SessionSupervisor(session, client_id=client_id_for("smoke"), clock=clock)

    _log(f"connecting read-only to {args.host}:{args.port} ...")
    supervisor.connect()
    if not supervisor.is_healthy():
        raise RuntimeError("session did not report connected")
    _log("connected (read-only; no order endpoint is called)")

    request = SurfaceJobRequest(
        symbol=args.symbol, trade_date=trade_date, selection=selection,
        market_data_type=args.market_data_type,
    )
    _log(f"building surface for {args.symbol} (collecting ~{args.seconds:.0f}s) ...")
    result = build_surface(
        request=request, store=store, config=config, config_hash=config_hash(config),
        supervisor=supervisor, clock=clock, correlation_id=f"surface-{args.symbol}",
        diagnostics=session,
    )
    supervisor.disconnect()
    _log(f"outputs persisted under {data_root}")
    return _render(args.symbol, result)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 - an operator script prints the failure, not a traceback
        _log(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
