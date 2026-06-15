"""A thin, request-frugal single-name Eurex option ENTITLEMENT PROBE (T-§7.4 pre-flight).

One capture run currently does two jobs at once: prove single-name Eurex option entitlement across
all ~50 index constituents (a yes/no per name) *and* capture the full tradeable basket for
analytics. The first job is the real unknown on a paper/trial account, yet today we pay the full
discovery+capture cost (hundreds of throttled requests per name) to answer a boolean. This script
runs only the cheap probe: per constituent it resolves the equity conid, fetches the option months,
and snapshots ONE near-ATM call+put to ask "does this name return a tradeable two-sided Eurex option
quote?" — a handful of read-only calls per name, not hundreds. Full-depth capture then targets only
the names this proves out.

It is a thin shim, like ``scripts/eod_run.py`` / ``scripts/ingest_membership.py``: the real logic
lives in the gate-tested ``algotrading.infra_ibkr.collectors.cp_rest_entitlement_probe`` and the
``CalendarResolver`` / registry / membership resolvers; this file only loads ``.env``, builds the
live CP Gateway transport, resolves the trade date, and prints the result. It is READ-ONLY: it
reads membership from the store and hits the gateway's read endpoints; it writes nothing.

The CP Gateway ceiling is a hard 10 req/s; this probe is deliberately frugal so a 50-name sweep
stays comfortably inside it. The gateway session is the same local ``build_gateway_session`` path
the live capture uses (browser-login cookie, no OAuth enrolment).

Usage:
    uv run python scripts/entitlement_probe.py --index SX5E
    uv run python scripts/entitlement_probe.py --index SX5E --top-n 50
    uv run python scripts/entitlement_probe.py --index SX5E --trade-date 2026-06-12
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime

from algotrading.core.paths import data_root, load_env_file, repo_root
from algotrading.infra.observability import configure_logging
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import CalendarResolver, load_index_registry
from algotrading.infra_ibkr.collectors.cp_rest_entitlement_probe import (
    format_probe_table,
    probe_index_entitlement,
)
from algotrading.infra_ibkr.session_factory import build_gateway_session


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe single-name Eurex option entitlement for an index's top-N constituents."
    )
    parser.add_argument("--index", required=True, help="Index symbol, e.g. SX5E.")
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Number of top-by-weight constituents to probe (default: the universe config's "
        "constituent_top_n).",
    )
    parser.add_argument(
        "--trade-date",
        type=date.fromisoformat,
        default=None,
        help="Trade date (ISO) to reconstruct membership as of. Default: today (UTC). A future "
        "date is rejected (no look-ahead).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    index_symbol = args.index.upper()

    trade_date = args.trade_date or datetime.now(UTC).date()
    if trade_date > datetime.now(UTC).date():
        print(f"[FAIL] --trade-date {trade_date.isoformat()} is in the future (no look-ahead)",
              file=sys.stderr)
        return 2

    registry = load_index_registry(repo_root() / "configs")
    try:
        index = registry.get(index_symbol)
    except Exception as exc:  # noqa: BLE001 — surface a labelled registry miss cleanly
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    # The trade date governs the close instant the calendar resolves; a non-session is a clean
    # nothing-to-probe exit, never a guessed instant. The probe itself snapshots *current* quotes
    # (CP REST has no historical option-quote endpoint), so the date drives only the membership
    # as-of and the session check, exactly like the live capture's no-look-ahead guard.
    resolver = CalendarResolver(registry, as_of=trade_date)
    if not resolver.is_session(index_symbol, trade_date):
        print(f"[skip] {index_symbol} {trade_date.isoformat()} is not a trading session")
        return 0

    top_n = args.top_n if args.top_n is not None else _config_top_n()

    print(
        f"[..] probing {index_symbol} top-{top_n} constituent option entitlement "
        f"(as_of={trade_date.isoformat()})"
    )
    transport, _session = build_gateway_session()
    store = ParquetStore(data_root())  # read-only: membership in, nothing written back
    result = probe_index_entitlement(
        transport, store=store, index=index, as_of_date=trade_date, top_n=top_n
    )
    print(format_probe_table(result))
    if not result.per_name:
        print(
            f"[FAIL] no banked membership for {index_symbol} as of {trade_date.isoformat()} — "
            f"nothing to probe; ingest a weighted source (scripts/ingest_membership.py)",
            file=sys.stderr,
        )
        return 1
    return 0


def _config_top_n() -> int:
    """The default probe breadth: the universe config's ``constituent_top_n`` (never a literal)."""
    from algotrading.core.config.loader import load_platform_config

    config = load_platform_config(repo_root() / "configs")
    return config.universe.constituent_top_n


if __name__ == "__main__":
    # The repo-root .env holds the IBKR_CP_* / IBKR_CP_GATEWAY settings the gateway session keys on
    # (neither `uv run` nor a systemd unit loads it). Load it at the one entrypoint before the
    # transport is built — the real environment still wins (override=False).
    load_env_file()
    configure_logging()
    raise SystemExit(main())
