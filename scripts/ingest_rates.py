"""Ingest the per-currency risk-free rate curve from the ECB Data Portal into the `rates` table.

The one-shot that finally populates the curve the analytics `r(T)`, Rho basis, and implied-vs-
riskfree spread QC read (ADR 0054). It loads the shipped `configs/rates.yaml` pillar set, pulls each
pillar's level from the ECB (rates/ecb_source.py), converts to the canonical continuous-ACT/365 zero
rate, and writes one provenance-stamped `RiskFreeRatePoint` per pillar, dated to the ECB observation
day (no look-ahead). A pillar the portal has no node for is a coverage gap, not a failure.

The feed and conversion live in the workspace package (importable, lint/mypy-checked, unit-tested in
`packages/infra/tests/test_rates_ecb_source.py`); this file is only the executable entrypoint, so it
may legitimately do the config-load + store-write wiring `scripts/` is allowed.

Usage:
    uv run python scripts/ingest_rates.py                       # EUR -> the canonical data store
    uv run python scripts/ingest_rates.py --currency EUR
    uv run python scripts/ingest_rates.py --store /tmp/rates    # a throwaway store (safe to probe)
    uv run python scripts/ingest_rates.py --as-of 2026-06-18    # override the stamp date
    uv run python scripts/ingest_rates.py --dry-run             # fetch + print, write nothing
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

from algotrading.core.config import load_platform_config
from algotrading.core.config.platform_config import config_hashes
from algotrading.core.paths import data_root
from algotrading.infra.rates import ingest_ecb_rates
from algotrading.infra.storage import ParquetStore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--currency", default="EUR", help="currency whose curve to ingest")
    parser.add_argument("--configs", default="configs", help="config bundle directory")
    parser.add_argument(
        "--store",
        default=None,
        help="store root to write into (default: the canonical data root)",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="override the publication/stamp date (ISO; default: the ECB observation date)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and print the pillars but write nothing",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = load_platform_config(args.configs)
    currency_config = config.rates.for_currency(args.currency)
    as_of = date.fromisoformat(args.as_of) if args.as_of else None

    points = ingest_ecb_rates(
        currency_config=currency_config,
        config_hashes=config_hashes(config),
        calc_ts=datetime.now(UTC),
        as_of=as_of,
    )

    obs = points[0].as_of
    print(f"{args.currency}: fetched {len(points)} pillar(s) as-of {obs.isoformat()}")
    for p in sorted(points, key=lambda r: r.maturity_years):
        print(
            f"  {p.pillar_tenor:>3} (T={p.maturity_years:.4f})  "
            f"r={p.rate * 100:.4f}%  [{p.diagnostics.instrument}]"
        )

    if args.dry_run:
        print("dry-run: nothing written")
        return 0

    root = Path(args.store) if args.store else data_root()
    ParquetStore(root).write("rates", list(points))
    print(f"wrote {len(points)} row(s) to {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
