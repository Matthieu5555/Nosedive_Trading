"""Render a 3D implied-vol surface from a captured raw day, to a standalone HTML.

Replays one stored raw day (provider/underlying/date) through the *exact* actor pipeline that runs
live — the canonical :func:`orchestration.build_surface` over a :class:`collectors.ReplaySource`
push adapter — and plots the reconstructed surface grid (IV vs days-to-expiry vs log-moneyness) with
the solved raw IV points overlaid. The canonical ``data/`` store is read **read-only**; the
re-derivation runs in a throwaway temp store, so nothing is ever written back to ``data/``. No
network. Open the resulting HTML in a browser.

Usage:
    uv run --group notebooks python scripts/plot_live_surface.py --symbol AAPL --date 2026-05-29
    uv run --group notebooks python scripts/plot_live_surface.py --symbol AAPL --out /tmp/surf.html
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import plotly.graph_objects as go
from algotrading.core.config import config_hash, load_platform_config
from algotrading.infra.actor.outputs import ActorOutputs
from algotrading.infra.collectors import ReplaySource, replay_day
from algotrading.infra.connectivity import ManualClock
from algotrading.infra.orchestration import SurfaceJobRequest, build_surface
from algotrading.infra.storage import ParquetStore

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_ROOT = _REPO_ROOT / "data"
_CONFIGS_DIR = _REPO_ROOT / "configs"
# Market-data type the replayed session records on its status (3 = delayed/last).
_MARKET_DATA_TYPE = 3


def _latest_day(store: ParquetStore, symbol: str) -> date | None:
    """Most recent committed trade date with raw events for ``symbol`` (or None)."""
    days = [d for d, u in store.list_partitions("raw_market_events") if u == symbol]
    return max(days) if days else None


def _reconstruct(store: ParquetStore, symbol: str, day: date) -> ActorOutputs:
    """Replay the stored day through ``build_surface`` in an isolated temp store.

    The canonical ``data/`` store is only read; the re-derivation writes to a throwaway temp store.
    """
    events = replay_day(store, day, underlying=symbol)
    if not events:
        raise SystemExit(f"No events for {symbol} on {day} in {store!r}.")
    masters = list(store.read("instrument_master", trade_date=day, underlying=symbol))
    config = load_platform_config(_CONFIGS_DIR)
    cfg_hash = config_hash(config)
    # Value as-of the last quote in the day — no look-ahead, reproducible from the events.
    as_of = max(event.canonical_ts for event in events)
    replay_source = ReplaySource(events)
    with TemporaryDirectory(prefix="plot-surface-") as tmp:
        temp_store = ParquetStore(Path(tmp))
        temp_store.write("instrument_master", masters)
        result = build_surface(
            request=SurfaceJobRequest(
                symbol=symbol,
                trade_date=day,
                market_data_type=_MARKET_DATA_TYPE,
                as_of=as_of,
                calc_ts=as_of,
                persist=False,
            ),
            store=temp_store,
            config=config,
            config_hash=cfg_hash,
            adapter=replay_source,
            masters=masters,
            drive=lambda _collector: replay_source.pump(),
            clock=ManualClock(start=as_of),
            correlation_id=f"plot-{symbol}-{day}",
        )
    return result.outputs


def _figure(outputs: ActorOutputs, symbol: str, day: date) -> go.Figure:
    """Build the 3D surface (IV % over days-to-expiry x moneyness) with solved IV points overlaid.

    The persisted ``surface_grid`` is one row per (maturity, moneyness bucket) carrying total
    variance ``w``; IV % is ``sqrt(w / T) * 100``. Rows are pivoted into a maturity x bucket mesh.
    """
    by_maturity: dict[float, dict[float, float]] = defaultdict(dict)
    for g in outputs.surface_grid:
        by_maturity[g.maturity_years][g.moneyness_bucket] = g.total_variance
    if not by_maturity:
        raise SystemExit(f"No surface grid produced for {symbol} on {day} (too few IV points?).")

    t_years = np.array(sorted(by_maturity), dtype=float)
    buckets = np.array(sorted({b for row in by_maturity.values() for b in row}), dtype=float)
    w = np.array(
        [[by_maturity[t].get(b, np.nan) for b in buckets] for t in t_years], dtype=float
    )
    iv_pct = np.sqrt(np.clip(w, 0.0, None) / t_years[:, None]) * 100.0
    dte = t_years * 365.0

    solved = [p for p in outputs.iv_points if p.iv is not None and p.iv > 0.0]
    spot = float(outputs.snapshots[0].reference_spot) if outputs.snapshots else float("nan")
    # Days-to-expiry per IV point: total_variance = iv^2 * T, so T = w / iv^2 (no key parsing).
    point_dte = [(p.total_variance / (p.iv * p.iv)) * 365.0 for p in solved]

    fig = go.Figure()
    fig.add_trace(
        go.Surface(
            x=buckets,
            y=dte,
            z=iv_pct,
            colorscale="Viridis",
            opacity=0.86,
            colorbar={"title": "IV %"},
            name="SVI surface",
        )
    )
    fig.add_trace(
        go.Scatter3d(
            x=[p.k for p in solved],
            y=point_dte,
            z=[p.iv * 100.0 for p in solved],
            mode="markers",
            marker={"size": 2.5, "color": "crimson"},
            name="solved IV points",
        )
    )
    fig.update_layout(
        title=(
            f"{symbol} vol surface — {day} "
            f"(spot {spot:.2f}, {len(solved)} IV pts, {len(t_years)} maturities)"
        ),
        scene={
            "xaxis_title": "log-moneyness ln(K/F)",
            "yaxis_title": "days to expiry",
            "zaxis_title": "implied vol (%)",
        },
        margin={"l": 0, "r": 0, "t": 40, "b": 0},
    )
    return fig


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plot a vol surface from a stored raw day -> standalone HTML (read-only)"
    )
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument(
        "--date", default=None, help="trade date YYYY-MM-DD (default: latest stored day)"
    )
    parser.add_argument(
        "--store-root", default=None, help=f"raw store root (default: {_DATA_ROOT})"
    )
    parser.add_argument("--out", default=None, help="output HTML path (never under data/)")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    store = ParquetStore(Path(args.store_root) if args.store_root else _DATA_ROOT)
    day = date.fromisoformat(args.date) if args.date else _latest_day(store, symbol)
    if day is None:
        print(f"No stored day for {symbol} in {store!r}.")
        return 1

    outputs = _reconstruct(store, symbol, day)
    fig = _figure(outputs, symbol, day)

    out = Path(args.out) if args.out else _REPO_ROOT / f"live_surface_{symbol}_{day}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out))
    solved = sum(1 for p in outputs.iv_points if p.iv is not None)
    print(f"wrote {out}  ({len(outputs.surface_grid)} grid rows, {solved} solved IV pts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
