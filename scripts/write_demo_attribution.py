"""Populate ``scenario_attributions`` with the realized day-over-day explain for the demo book.

The Attribution tab reads ``scenario_attributions`` via ``GET /api/attribution`` but nothing
wrote that table for the demo book, so the tab was permanently empty. This writer runs the
realized, fixed-expiry decomposition (the same engine + resolution the
``GET /api/attribution/realized`` endpoint uses) for the September SX5E straddle across the
banked closes, and persists one book row + one position row per leg per day-step.

A realized day-step (close[d] -> close[d+1]) is stamped onto the persisted ``ScenarioAttribution``
schema as: ``valuation_ts`` = the end-of-step close, ``scenario_id`` = ``realized:<start>-><end>``,
and the realized market move carried in ``spot_shock`` / ``vol_shock`` / ``time_shock`` (the
fractional spot move, the vol-point move, and the year-fraction of time decay). The seven Taylor
terms, full_reprice, residual and verdict come straight off the engine output.

ALWAYS validate writes against a TEMP store first, then the canonical store. The canonical parquet
under ``data/`` is untracked and not git-recoverable, so a write is proven against a throwaway root
before it ever touches ``data/``.

Usage:
    # validate against a temp root only (default — never touches canonical)
    uv run python scripts/write_demo_attribution.py

    # after validating, write to canonical data/
    uv run python scripts/write_demo_attribution.py --canonical
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from algotrading.core.paths import repo_root
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.frontend.realized_attribution import (
    BookSpec,
    RealizedDayStep,
    attribute_day_steps,
    september_straddle_spec,
)
from algotrading.infra.contracts import ScenarioAttribution
from algotrading.infra.risk.attribution import RealizedLineAttribution
from algotrading.infra.risk.config import AttributionConfig
from algotrading.infra.storage import ParquetStore

_TABLE = "scenario_attributions"
_BOOK_CONTRACT_KEY = "__book__"
_LEVEL_BOOK = "book"
_LEVEL_POSITION = "position"
_ATTRIBUTION_VERSION = "realized-bff-v1"
_SCENARIO_VERSION = "realized-fixed-expiry-v1"
_CODE_VERSION = "write_demo_attribution-v1"
_UNDERLYING = "SX5E"


def _scenario_id(step: RealizedDayStep) -> str:
    return f"realized:{step.start_date.isoformat()}->{step.end_date.isoformat()}"


def _close_ts(step: RealizedDayStep) -> datetime:
    # 17:30 CET = 15:30 UTC: the OESX option settlement instant for SX5E.
    return datetime(
        step.end_date.year, step.end_date.month, step.end_date.day, 15, 30, tzinfo=UTC
    )


def _move_shocks(line: RealizedLineAttribution) -> tuple[float, float, float]:
    """Map the realized move onto the persisted (spot_shock, vol_shock, time_shock) columns.

    spot_shock is fractional (the engine's convention); vol_shock and time_shock are the
    absolute vol-point and year-fraction moves the decomposition actually saw.
    """
    spot0 = line.start.valuation.spot
    spot_shock = (line.move.d_spot / spot0) if spot0 else 0.0
    return spot_shock, line.move.d_vol, line.move.d_time


def _prov(step: RealizedDayStep) -> ProvenanceStamp:
    return stamp(
        calc_ts=datetime.now(UTC),
        code_version=_CODE_VERSION,
        config_hashes={"attribution": _ATTRIBUTION_VERSION},
        source_records=(
            source_ref("iv_points", _UNDERLYING, step.start_date.isoformat()),
            source_ref("iv_points", _UNDERLYING, step.end_date.isoformat()),
        ),
        source_timestamps=(_close_ts(step),),
        as_of=step.end_date,
    )


def _rows_for_step(spec: BookSpec, step: RealizedDayStep) -> list[ScenarioAttribution]:
    attribution = step.attribution
    valuation_ts = _close_ts(step)
    provenance = _prov(step)
    scenario_id = _scenario_id(step)
    config = attribution.config
    # Book-level move shocks: take them off the first leg (the straddle legs share spot/time;
    # vol differs slightly by leg, the book row records the representative move).
    spot_shock, vol_shock, time_shock = (
        _move_shocks(attribution.lines[0]) if attribution.lines else (0.0, 0.0, 0.0)
    )

    rows: list[ScenarioAttribution] = [
        ScenarioAttribution(
            valuation_ts=valuation_ts,
            portfolio_id=spec.portfolio_id,
            scenario_id=scenario_id,
            contract_key=_BOOK_CONTRACT_KEY,
            level=_LEVEL_BOOK,
            spot_shock=spot_shock,
            vol_shock=vol_shock,
            time_shock=time_shock,
            delta_pnl=attribution.terms.delta_pnl,
            gamma_pnl=attribution.terms.gamma_pnl,
            vega_pnl=attribution.terms.vega_pnl,
            theta_pnl=attribution.terms.theta_pnl,
            rho_pnl=attribution.terms.rho_pnl,
            vanna_pnl=attribution.terms.vanna_pnl,
            volga_pnl=attribution.terms.volga_pnl,
            approx_pnl=attribution.terms.total,
            full_reprice_pnl=attribution.full_reprice_pnl,
            residual=attribution.residual,
            within_tolerance=attribution.within_tolerance,
            residual_abs_tol=config.residual_abs_tol,
            residual_rel_tol=config.residual_rel_tol,
            scenario_version=_SCENARIO_VERSION,
            attribution_version=_ATTRIBUTION_VERSION,
            source_snapshot_ts=valuation_ts,
            provenance=provenance,
        )
    ]
    for line in attribution.lines:
        leg_spot_shock, leg_vol_shock, leg_time_shock = _move_shocks(line)
        rows.append(
            ScenarioAttribution(
                valuation_ts=valuation_ts,
                portfolio_id=spec.portfolio_id,
                scenario_id=scenario_id,
                contract_key=line.contract_key,
                level=_LEVEL_POSITION,
                spot_shock=leg_spot_shock,
                vol_shock=leg_vol_shock,
                time_shock=leg_time_shock,
                delta_pnl=line.terms.delta_pnl,
                gamma_pnl=line.terms.gamma_pnl,
                vega_pnl=line.terms.vega_pnl,
                theta_pnl=line.terms.theta_pnl,
                rho_pnl=line.terms.rho_pnl,
                vanna_pnl=line.terms.vanna_pnl,
                volga_pnl=line.terms.volga_pnl,
                approx_pnl=line.terms.total,
                full_reprice_pnl=line.full_reprice_pnl,
                residual=line.residual,
                within_tolerance=line.within_tolerance,
                residual_abs_tol=config.residual_abs_tol,
                residual_rel_tol=config.residual_rel_tol,
                scenario_version=_SCENARIO_VERSION,
                attribution_version=_ATTRIBUTION_VERSION,
                source_snapshot_ts=valuation_ts,
                provenance=provenance,
            )
        )
    return rows


def _banked_dates(store: ParquetStore) -> list:
    return sorted(
        part_date
        for part_date, part_underlying in store.list_partitions("iv_points")
        if part_underlying == _UNDERLYING
    )


def build_rows(store: ParquetStore) -> tuple[BookSpec, list[ScenarioAttribution]]:
    spec = september_straddle_spec()
    dates = _banked_dates(store)
    if len(dates) < 2:
        raise SystemExit(
            f"need >=2 banked {_UNDERLYING} iv_points dates, found {[d.isoformat() for d in dates]}"
        )
    config = AttributionConfig(version=_ATTRIBUTION_VERSION)
    steps = attribute_day_steps(store, spec, dates, config)
    rows: list[ScenarioAttribution] = []
    for step in steps:
        rows.extend(_rows_for_step(spec, step))
    return spec, rows


def _write_and_read_back(source_store: ParquetStore, target_root: Path) -> int:
    """Build rows from ``source_store``, write into ``target_root``, read them back."""
    _spec, rows = build_rows(source_store)
    target = ParquetStore(target_root)
    target.write(_TABLE, rows)
    read_back = target.read(_TABLE)
    written = {(r.valuation_ts, r.contract_key, r.scenario_id) for r in rows}
    got = {(r.valuation_ts, r.contract_key, r.scenario_id) for r in read_back}
    missing = written - got
    if missing:
        raise SystemExit(f"read-back missing {len(missing)} rows: {sorted(missing)[:3]}")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--canonical",
        action="store_true",
        help="write to data/ (canonical) AFTER the temp validation passes",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=repo_root() / "data",
        help="canonical data root (default: <repo>/data)",
    )
    args = parser.parse_args()

    source = ParquetStore(args.data_root)
    spec, rows = build_rows(source)
    print(f"built {len(rows)} {_TABLE} rows for {spec.portfolio_id} from {args.data_root}")

    # ALWAYS validate against a throwaway temp root first.
    tmp_root = Path(tempfile.mkdtemp(prefix="attribution-validate-"))
    try:
        n = _write_and_read_back(source, tmp_root)
        print(f"temp validation OK: wrote+read back {n} rows under {tmp_root}")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    if not args.canonical:
        print("dry run complete (temp only). Re-run with --canonical to persist to data/.")
        return

    target = ParquetStore(args.data_root)
    target.write(_TABLE, rows)
    read_back = [r for r in target.read(_TABLE) if r.portfolio_id == spec.portfolio_id]
    print(f"wrote {len(rows)} rows to canonical {args.data_root}; {len(read_back)} now readable")


if __name__ == "__main__":
    main()
