"""IBKR historical daily-OHLC backfill — the operator one-shot for past underlying history (WS 1C).

Companion to ``scripts/eod_run.py``: where that captures *today's* option chain forward, this stacks
*past* underlying daily OHLC (the only history CP REST can serve — there is no historical
option-quote endpoint). A thin shim over ``algotrading.infra_ibkr.history_backfill``: the wiring
lives in the workspace package (importable, gate-checked, unit-tested against a fake transport);
this file is only the executable entrypoint, the one place that legitimately sees both the
workspace and the IBKR broker leaf (``scripts/`` is outside the root gate, so this cross-layer
wiring is allowed exactly here and nowhere in the packages).

It resolves the enabled indices from the 1J registry, each index's underlying conid (and, by
default, its as-of constituents' equity conids) at fetch time, then fetches + persists each daily
bars — resumable and idempotent on ``(provider, underlying, trade_date)`` (a run killed mid-way
re-fetches only the missing tail). A non-credentialed environment is a clean no-op (exit 0): no
IBKR CP OAuth artifacts, nothing to fetch — see ``.env.example`` for the ``IBKR_CP_*`` vars.

Usage:
    uv run python scripts/ohlc_backfill.py                    # all enabled indices + constituents
    uv run python scripts/ohlc_backfill.py --index SX5E --period 10y
    uv run python scripts/ohlc_backfill.py --no-constituents  # index underlyings only
    uv run python scripts/ohlc_backfill.py --as-of 2026-06-01 # basket as it stood on that date
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import structlog
from algotrading.core.config.loader import load_platform_config
from algotrading.infra.connectivity import load_env_file
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import index_registry_from_config
from algotrading.infra_ibkr.config import load_ibkr_history_config
from algotrading.infra_ibkr.history_backfill import (
    build_history_collector,
    history_requests_for,
)
from algotrading.infra_ibkr.session_factory import build_credentialed_session

_LOGGER = structlog.get_logger("ibkr.ohlc_backfill")

# This file: scripts/ohlc_backfill.py — its parent is the repo root that holds configs/ and data/.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIGS_DIR = _REPO_ROOT / "configs"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ohlc_backfill",
        description="Backfill IBKR daily OHLC for the enabled indices (+ constituents) — WS 1C.",
    )
    parser.add_argument(
        "--period",
        default=None,
        help="IBKR history window (e.g. 5y, 10y). Default: the config's default_period.",
    )
    parser.add_argument(
        "--index",
        default=None,
        help="Scope to a single index symbol (e.g. SX5E). Default: all enabled indices.",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="ISO date for the constituent basket (point-in-time). Default: today (UTC).",
    )
    parser.add_argument(
        "--no-constituents",
        action="store_true",
        help="Backfill only the index underlyings, not their constituents.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Resolve the requests and run the backfill; exit 0 on success or a clean no-credentials no-op.

    A non-credentialed environment returns 0 without touching the network (nothing to backfill).
    """
    args = _parse_args(argv)
    # Load the repo-root .env (the IBKR_CP_* credentials) before the credentialed session is built;
    # neither `uv run` nor the caller's shell does it. The real environment still wins over the file.
    load_env_file(_REPO_ROOT / ".env")
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(UTC).date()
    calc_ts = datetime.now(UTC)

    config = load_platform_config(_CONFIGS_DIR)
    registry = index_registry_from_config(config)
    history_config = load_ibkr_history_config()
    period = args.period or history_config.default_period
    data_root = Path(os.environ.get("ALGOTRADING_DATA_ROOT", str(_REPO_ROOT / "data")))
    store = ParquetStore(data_root)

    built = build_credentialed_session()
    if built is None:
        _LOGGER.info(
            "ibkr.ohlc_backfill.no_credentials",
            reason="no IBKR CP OAuth artifacts in environment; nothing to backfill (clean no-op)",
        )
        return 0
    transport, session = built

    collector = build_history_collector(
        store=store,
        calc_ts=calc_ts,
        transport=transport,
        session=session,
        config=history_config,
    )
    if collector is None:  # pragma: no cover — credentialed session implies a collector
        return 0

    requests = history_requests_for(
        store=store,
        registry=registry,
        transport=transport,
        period=period,
        as_of_date=as_of,
        index=args.index,
        include_constituents=not args.no_constituents,
    )
    result = collector.backfill(requests, correlation_id=f"ohlc-backfill-{as_of.isoformat()}")
    _LOGGER.info(
        "ibkr.ohlc_backfill.done",
        period=period,
        as_of=as_of.isoformat(),
        fetched=len(result.fetched),
        skipped=len(result.skipped),
        bar_count=result.bar_count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
