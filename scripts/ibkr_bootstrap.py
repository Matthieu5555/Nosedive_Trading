"""Smoke-test a TWS / IB Gateway connection: connect, prove a round-trip, pull one snapshot.

The cheapest proof that a (headless) IB Gateway is actually answering, before anything heavier is
built on top of it (the gated capture pipeline, roadmap 1C/1G, comes later). It is read-only and
makes no compute decisions: it drives the existing
:class:`~algotrading.infra_ibkr.connectivity.ibkr_transport.IbkrTransport`, draws a reserved
"smoke" client id so it cannot collide with the real services, and asks for **delayed** data
(``market_data_type=3``), which is entitlement-free.

Steps, each a PASS/FAIL line:
  1. connect to host:port with a smoke client id;
  2. broker round-trip (``reqCurrentTime``) + clock skew vs the local UTC clock;
  3. resolve the underlying on ``SMART`` (the right exchange — a specific exchange + arbitrary
     strike is what returns "Error 200, no security definition");
  4. one snapshot for that underlying; report bid/ask/last/close.

Exit codes (mirroring the supervisor's 0/1/2 convention):
  0 = healthy (connected, round-trip, and a quote came back)
  1 = hard failure (could not connect, or no round-trip — the box/Gateway is down)
  2 = soft failure (connected and clock OK, but no quote — e.g. an entitlement wall, Error 10091,
      or the symbol did not resolve)

Config is read from a repo-root ``.env`` (see ``.env.example``) and/or the environment; explicit
CLI flags win. Output is ASCII-only on purpose (a non-ASCII char on a cp1252 console raises
``'charmap' codec can't encode``).

Usage:
    uv run --extra ibkr python scripts/ibkr_bootstrap.py
    uv run --extra ibkr python scripts/ibkr_bootstrap.py --symbol SPY --port 4002
    uv run --extra ibkr python scripts/ibkr_bootstrap.py --market-data-type 2   # frozen (at close)
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Exit codes.
_OK = 0
_HARD = 1
_SOFT = 2

# IB market-data types: 1 live, 2 frozen (last at close), 3 delayed (free), 4 delayed-frozen.
_DEFAULT_MARKET_DATA_TYPE = 3


def _load_dotenv(path: Path) -> None:
    """Minimal, dependency-free ``.env`` loader. Already-exported vars win (``setdefault``)."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--symbol", default="SPY", help="underlying to snapshot (default: SPY)")
    p.add_argument("--host", default=None, help="Gateway host (default: $IBKR_HOST or 127.0.0.1)")
    p.add_argument("--port", type=int, default=None, help="Gateway port ($IBKR_PORT or 4002)")
    p.add_argument(
        "--client-id",
        type=int,
        default=None,
        help="socket client id (default: $IBKR_CLIENT_ID, else a reserved 'smoke' id)",
    )
    p.add_argument(
        "--market-data-type",
        type=int,
        default=_DEFAULT_MARKET_DATA_TYPE,
        choices=(1, 2, 3, 4),
        help="1 live / 2 frozen / 3 delayed (default, free) / 4 delayed-frozen",
    )
    p.add_argument("--connect-timeout", type=float, default=10.0, help="connect timeout seconds")
    p.add_argument("--ping-timeout", type=float, default=5.0, help="round-trip timeout seconds")
    return p.parse_args(argv)


def _resolve_endpoint(args: argparse.Namespace) -> tuple[str, int, int]:
    """CLI flag > env var > documented default. The smoke client id is reserved, not arbitrary."""
    from algotrading.infra.connectivity import client_id_for

    host = args.host or os.environ.get("IBKR_HOST", "127.0.0.1")
    port = args.port if args.port is not None else int(os.environ.get("IBKR_PORT", "4002"))
    if args.client_id is not None:
        client_id = args.client_id
    elif os.environ.get("IBKR_CLIENT_ID"):
        client_id = int(os.environ["IBKR_CLIENT_ID"])
    else:
        client_id = client_id_for("smoke")
    return host, port, client_id


def _has_quote(*values: float) -> bool:
    """True if any field is a real (non-nan) number — delayed feeds often fill only some."""
    return any(v is not None and not math.isnan(v) for v in values)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _load_dotenv(_REPO_ROOT / ".env")

    # Import the broker stack only now, so --help works without the optional ibkr extra installed.
    try:
        from algotrading.infra.connectivity import SystemClock
        from algotrading.infra.connectivity.session import TransportError
        from algotrading.infra_ibkr.connectivity.ibkr_transport import IbkrTransport
        from ib_async import Stock
    except ImportError as exc:
        print(f"[FAIL] cannot import the IBKR stack ({exc}).")
        print("       Install the optional extra: uv run --extra ibkr python scripts/...")
        return _HARD

    host, port, client_id = _resolve_endpoint(args)
    print(f"[..] connecting to {host}:{port} (client id {client_id}) ...")

    transport = IbkrTransport(connect_timeout=args.connect_timeout, ping_timeout=args.ping_timeout)
    try:
        transport.open(host, port, client_id)
    except TransportError as exc:
        print(f"[FAIL] could not connect: {exc}")
        print("       Is TWS/IB Gateway running, API enabled, and this host:port correct?")
        return _HARD
    print("[OK] connected.")

    try:
        # 2. Round-trip + clock skew.
        try:
            broker_time = transport.current_time()
        except TransportError as exc:
            print(f"[FAIL] no round-trip: {exc}")
            return _HARD
        skew = (SystemClock().now() - broker_time).total_seconds()
        print(f"[OK] round-trip; broker clock {broker_time.isoformat()} (skew {skew:+.2f}s).")

        # 3. Resolve the underlying on SMART.
        ib = transport.ib
        ib.reqMarketDataType(args.market_data_type)
        contract = Stock(args.symbol, "SMART", "USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            print(f"[FAIL] could not resolve {args.symbol} on SMART (no security definition).")
            return _SOFT
        print(f"[OK] resolved {args.symbol} (conId {qualified[0].conId}).")

        # 4. One snapshot.
        (ticker,) = ib.reqTickers(contract)
        if not _has_quote(ticker.bid, ticker.ask, ticker.last, ticker.close):
            print(f"[WARN] no quote for {args.symbol} (entitlement wall / 10091, or market shut).")
            print("       Delayed (type 3) is free; type 2 (frozen) returns the last close.")
            return _SOFT
        print(
            f"[OK] snapshot {args.symbol}: "
            f"bid={ticker.bid} ask={ticker.ask} last={ticker.last} close={ticker.close}"
        )
    finally:
        transport.close()

    print("[OK] healthy.")
    return _OK


if __name__ == "__main__":
    sys.exit(main())
