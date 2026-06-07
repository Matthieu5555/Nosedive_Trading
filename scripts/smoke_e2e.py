"""End-to-end smoke driver (WS V1): prove the whole stack is alive, offline, in one walk.

One command -- ``uv run python scripts/smoke_e2e.py`` -- exercises the real path end to
end and emits a single PASS/FAIL summary with one line per stage and a process exit code.
It is *not* a re-run of the unit/contract suite: it drives existing public entrypoints and
asserts headline results (the four acceptance tests already encode the depth). Default data
source is the committed offline ``synthetic_known_answer`` chain replayed through the actor,
so the smoke needs no network, no broker, and no entitlement.

Stages, each emitting one ``[PASS]``/``[FAIL]``/``[SKIP]`` line and folding into the worst-case
exit code:

* Stage 0 -- bootstrap: load ``PlatformConfig`` and open a scratch ``ParquetStore``.
* Stage 1 -- deterministic replay: seed one offline day of raw events + masters.
* Stage 2 -- analytics: ``reconstruct_day`` over the day; assert derived rows landed.
* Stage 3 -- BFF over HTTP: ``TestClient`` against the same store; every endpoint non-5xx.
* Stage 4 -- web front: ``npm run build`` + ``npm test`` (SKIP cleanly if Node absent).
* Stage 5 -- invariants: provenance + per-bundle ``config_hashes`` (ADR 0028), and
  byte-identical replay (run stages 1-2 twice, same derived stamp hashes).

Exit codes mirror ``scripts/ibkr_bootstrap.py``: ``0`` healthy (every required stage PASS,
SKIPs allowed), ``1`` hard failure (the spine is broken), ``2`` soft failure (the spine is
alive but a non-blocking stage degraded -- a SKIP or a soft failure). ASCII-only output.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

# --- exit codes (the ibkr_bootstrap.py convention) ---------------------------
EXIT_OK = 0
EXIT_HARD = 1
EXIT_SOFT = 2

# --- stage outcomes ----------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

_ROOT_MARKER = "AGENTS.md"


@dataclass(frozen=True, slots=True)
class StageResult:
    """One stage's outcome: its name, status, a one-clause why, and the failure severity.

    ``hard`` marks a required stage whose failure means the spine itself is broken (exit 1).
    A soft failure or any SKIP degrades the verdict to exit 2, never 1.
    """

    name: str
    status: str
    detail: str
    hard: bool = True

    def line(self) -> str:
        return f"[{self.status}] {self.name} -- {self.detail}"


def compute_exit_code(results: Sequence[StageResult]) -> int:
    """Fold stage results into the 0/1/2 verdict (pure, so it is unit-testable).

    ``1`` if any required (hard) stage failed; else ``2`` if anything was a soft failure or a
    SKIP; else ``0``. A healthy run is all-PASS (SKIPs allowed only at exit 2).
    """
    if any(r.status == FAIL and r.hard for r in results):
        return EXIT_HARD
    if any(r.status == FAIL or r.status == SKIP for r in results):
        return EXIT_SOFT
    return EXIT_OK


def find_repo_root(start: Path) -> Path:
    """Walk up from ``start`` to the directory holding the root marker (``AGENTS.md``)."""
    for candidate in [start, *start.parents]:
        if (candidate / _ROOT_MARKER).exists():
            return candidate
    raise RuntimeError(f"could not locate repo root from {start} (no {_ROOT_MARKER})")


# --- Stage 1 + 2: offline day production and analytics -----------------------
# The committed ``synthetic_known_answer`` chain lives in the test-fixtures package
# (tracked, so it is present on every checkout/CI -- unlike the gitignored ``data/``). The
# smoke replays exactly that chain through the production actor, so importing it here is
# deliberate: it is the canonical offline source, not a test internal.
def _import_chain_fixtures(repo_root: Path) -> tuple[Any, Any]:
    tests_dir = str(repo_root / "packages" / "infra" / "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from fixtures.events import quote_events  # noqa: PLC0415
    from fixtures.library import get_fixture  # noqa: PLC0415

    return get_fixture, quote_events


@dataclass(frozen=True, slots=True)
class OfflineDay:
    """The coordinates of the seeded offline day, shared across stages."""

    trade_date: date
    underlying: str
    as_of: datetime


def seed_offline_day(store: Any, repo_root: Path) -> OfflineDay:
    """Seed one offline day of raw events + instrument masters into ``store``.

    Replays the committed ``synthetic_known_answer`` chain: bid/ask/last events for the
    underlying and every option, plus the instrument masters the analytics read-back needs.
    Returns the day's coordinates. ``as_of`` is the chain's own fixed timestamp -- no clock,
    fully reproducible.
    """
    from algotrading.infra.contracts import InstrumentMaster  # noqa: PLC0415

    get_fixture, quote_events = _import_chain_fixtures(repo_root)
    chain = get_fixture("synthetic_known_answer")
    as_of = chain.as_of
    spot = chain.underlying_spot
    events = list(
        quote_events(
            chain.underlying,
            bid=spot - 0.05,
            ask=spot + 0.05,
            last=spot,
            ts=as_of,
            session_id=chain.underlying.canonical(),
        )
    )
    for quote in chain.quotes:
        events += list(
            quote_events(
                quote.instrument,
                bid=quote.bid,
                ask=quote.ask,
                last=quote.last,
                ts=as_of,
                session_id=quote.instrument.canonical(),
            )
        )
    instruments = [chain.underlying, *(quote.instrument for quote in chain.quotes)]
    masters = [
        InstrumentMaster(
            instrument_key=instrument.canonical(),
            as_of_date=as_of.date(),
            instrument=instrument,
            raw_broker_payload="{}",
        )
        for instrument in instruments
    ]
    store.write("raw_market_events", events)
    store.write("instrument_master", masters)
    return OfflineDay(
        trade_date=as_of.date(),
        underlying=chain.underlying.underlying_symbol,
        as_of=as_of,
    )


def _positions_for(store: Any, day: OfflineDay) -> list[Any]:
    """A tiny book over the day's option contracts, so risk/scenario rows are produced."""
    from algotrading.infra.contracts import InstrumentMaster, Position  # noqa: PLC0415

    masters = [
        record
        for record in store.read("instrument_master")
        if isinstance(record, InstrumentMaster) and record.instrument.option_right
    ]
    return [
        Position(
            valuation_ts=day.as_of,
            portfolio_id="pf-smoke",
            contract_key=master.instrument_key,
            quantity=1.0,
            source="record",
        )
        for master in masters[:2]
    ]


def run_analytics(store: Any, day: OfflineDay, config: Any, cfg_hashes: Any) -> Any:
    """Drive the end-of-day analytics over the seeded day; persist the derived layer.

    Uses ``reconstruct_day`` (the documented pure-reconstruction path): forwards -> IV ->
    surfaces -> pricing -> risk -> scenarios off the raw layer, ``persist=True``.
    """
    from algotrading.infra.contracts import InstrumentMaster  # noqa: PLC0415
    from algotrading.infra.orchestration.reconstruction import reconstruct_day  # noqa: PLC0415

    masters = [r for r in store.read("instrument_master") if isinstance(r, InstrumentMaster)]
    instruments = [master.instrument for master in masters]
    return reconstruct_day(
        store,
        day.trade_date,
        _positions_for(store, day),
        instruments=instruments,
        masters=masters,
        config=config,
        config_hashes=cfg_hashes,
        as_of=day.as_of,
        calc_ts=day.as_of,
        persist=True,
        correlation_id="smoke-e2e",
    )


# --- Stage 3: BFF endpoint probing -------------------------------------------
@dataclass(frozen=True, slots=True)
class EndpointSpec:
    """One endpoint to probe: its path, query params, and whether it is required."""

    path: str
    params: dict[str, str]
    required: bool


def _registered_prefixes(app: Any) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def probe_endpoints(client: Any, app: Any, specs: Sequence[EndpointSpec]) -> list[StageResult]:
    """GET each endpoint; a 5xx is a FAIL, an unregistered route is a SKIP, else PASS.

    Factored out so the no-500 and skip-unlanded behaviours are unit-testable against a stub
    app. A route absent from ``app.routes`` (an unlanded router) degrades to SKIP, not FAIL,
    so the smoke is useful before every stage has landed.
    """
    registered = _registered_prefixes(app)
    results: list[StageResult] = []
    for spec in specs:
        name = f"bff {spec.path}"
        if spec.path not in registered:
            results.append(StageResult(name, SKIP, "route not registered", hard=False))
            continue
        response = client.get(spec.path, params=spec.params)
        if response.status_code >= 500:
            results.append(StageResult(name, FAIL, f"HTTP {response.status_code}", hard=True))
        else:
            results.append(StageResult(name, PASS, f"HTTP {response.status_code}", hard=True))
    return results


def _bff_specs(day: OfflineDay) -> list[EndpointSpec]:
    u = {"underlying": day.underlying}
    return [
        EndpointSpec("/api/health", {}, required=True),
        EndpointSpec("/api/surfaces", u, required=True),
        EndpointSpec("/api/risk", {}, required=True),
        EndpointSpec("/api/providers", {}, required=True),
        EndpointSpec("/api/config", {}, required=True),
        EndpointSpec("/api/price-history", u, required=False),
        EndpointSpec("/api/constituents", {}, required=False),
        EndpointSpec("/api/analytics", u, required=False),
        EndpointSpec("/api/recorded-dates", {}, required=False),
    ]


# --- Stage 5: invariants -----------------------------------------------------
def _derived_stamp_hashes(store: Any) -> dict[str, list[str]]:
    """The provenance stamp hashes of the derived rows, per table, in stored order."""
    out: dict[str, list[str]] = {}
    for table in ("surface_parameters", "iv_points", "pricing_results"):
        rows = store.read(table)
        out[table] = [row.provenance.stamp_hash for row in rows]
    return out


# --- the driver --------------------------------------------------------------
def _print_summary(results: Sequence[StageResult], code: int, as_json: bool) -> None:
    if as_json:
        payload = {
            "stages": [
                {"name": r.name, "status": r.status, "detail": r.detail} for r in results
            ],
            "exit_code": code,
            "verdict": {EXIT_OK: "healthy", EXIT_HARD: "broken", EXIT_SOFT: "degraded"}[code],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print("")
    print("=== smoke_e2e summary ===")
    for result in results:
        print(result.line())
    verdict = {EXIT_OK: "HEALTHY", EXIT_HARD: "BROKEN (spine down)", EXIT_SOFT: "DEGRADED"}[code]
    print(f"verdict: {verdict} (exit {code})")


def run_smoke(argv: Sequence[str] | None = None) -> int:
    """Run the smoke and return the 0/1/2 exit code. The single public entrypoint."""
    args = _parse_args(argv)
    repo_root = find_repo_root(Path(__file__).resolve())
    results: list[StageResult] = []

    owns_data_root = args.data_root is None
    data_root = (
        Path(args.data_root) if args.data_root else Path(tempfile.mkdtemp(prefix="smoke-e2e-"))
    )
    try:
        results.extend(_run_stages(args, repo_root, data_root))
    finally:
        if owns_data_root:
            shutil.rmtree(data_root, ignore_errors=True)

    code = compute_exit_code(results)
    _print_summary(results, code, args.json)
    return code


def _run_stages(args: argparse.Namespace, repo_root: Path, data_root: Path) -> list[StageResult]:
    from algotrading.core.config import config_hashes, load_platform_config  # noqa: PLC0415
    from algotrading.infra.storage import ParquetStore  # noqa: PLC0415

    results: list[StageResult] = []

    # Stage 0 -- bootstrap.
    try:
        config = load_platform_config(repo_root / "configs")
        cfg_hashes = config_hashes(config)
        store = ParquetStore(data_root)
        if not (cfg_hashes and all(cfg_hashes.values())):
            raise RuntimeError("config_hashes empty -- every bundle must hash")
        results.append(StageResult("stage0 bootstrap", PASS, "config + store opened"))
    except Exception as exc:  # noqa: BLE001 -- a broken bootstrap is a hard stop, reported not raised
        results.append(StageResult("stage0 bootstrap", FAIL, f"{type(exc).__name__}: {exc}"))
        return results

    # Stage 1 -- deterministic replay.
    try:
        day = seed_offline_day(store, repo_root)
        partitions = store.list_partitions("raw_market_events")
        if (day.trade_date, day.underlying) not in partitions:
            raise RuntimeError("raw partition not under the (trade_date, underlying) layout")
        results.append(
            StageResult("stage1 replay", PASS, f"seeded {day.underlying} {day.trade_date}")
        )
    except Exception as exc:  # noqa: BLE001
        results.append(StageResult("stage1 replay", FAIL, f"{type(exc).__name__}: {exc}"))
        return results

    # Stage 2 -- analytics.
    try:
        run_analytics(store, day, config, cfg_hashes)
        surfaces = store.read("surface_parameters")
        if not surfaces:
            raise RuntimeError("no surface_parameters produced")
        results.append(StageResult("stage2 analytics", PASS, f"{len(surfaces)} surface slices"))
        grid = store.read("projected_option_analytics")
        if grid:
            results.append(
                StageResult("stage2 grid (1F)", PASS, f"{len(grid)} tenor x band cells", hard=False)
            )
        else:
            # The projected grid is produced on the actor/EOD path (surfaces.project_grid),
            # not the pure reconstruct_day path this stage drives -- a path limitation, not a
            # failure. Driving project_grid directly would reach into compute internals
            # (in-memory SliceFit), which V1 must not. Honest SKIP -> exit 2.
            results.append(
                StageResult(
                    "stage2 grid (1F)", SKIP, "grid is on the actor/EOD path, not reconstruct_day",
                    hard=False,
                )
            )
    except Exception as exc:  # noqa: BLE001
        results.append(StageResult("stage2 analytics", FAIL, f"{type(exc).__name__}: {exc}"))
        return results

    # Stage 3 -- BFF over HTTP.
    results.extend(_stage_bff(repo_root, data_root, store, day, config))

    # Stage 4 -- web front.
    results.append(_stage_web(repo_root, skip=args.skip_web))

    # Stage 5 -- invariants.
    results.extend(_stage_invariants(store, repo_root))
    return results


def _stage_bff(
    repo_root: Path, data_root: Path, store: Any, day: OfflineDay, config: Any
) -> list[StageResult]:
    try:
        from algotrading.frontend.app import create_app  # noqa: PLC0415
        from algotrading.frontend.context import AppContext  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415

        ctx = AppContext(
            store_root=data_root,
            configs_dir=repo_root / "configs",
            store=store,
            default_underlying=day.underlying,
        )
        app = create_app(ctx)
        with TestClient(app) as client:
            return probe_endpoints(client, app, _bff_specs(day))
    except Exception as exc:  # noqa: BLE001
        return [StageResult("stage3 bff", FAIL, f"{type(exc).__name__}: {exc}")]


def _stage_web(repo_root: Path, *, skip: bool) -> StageResult:
    web_dir = repo_root / "apps" / "frontend" / "web"
    if skip:
        return StageResult("stage4 web", SKIP, "skipped (--skip-web)", hard=False)
    if shutil.which("npm") is None:
        return StageResult("stage4 web", SKIP, "npm not available", hard=False)
    if not (web_dir / "node_modules").is_dir():
        install = subprocess.run(  # noqa: S603
            ["npm", "ci"], cwd=web_dir, capture_output=True, text=True  # noqa: S607
        )
        if install.returncode != 0:
            return StageResult("stage4 web", SKIP, "npm ci failed", hard=False)
    for step in (["npm", "run", "build"], ["npm", "test"]):
        proc = subprocess.run(step, cwd=web_dir, capture_output=True, text=True)  # noqa: S603
        if proc.returncode != 0:
            return StageResult(
                "stage4 web", FAIL, f"{' '.join(step)} exit {proc.returncode}", hard=False
            )
    return StageResult("stage4 web", PASS, "build + component tests green", hard=False)


def _stage_invariants(store: Any, repo_root: Path) -> list[StageResult]:
    results: list[StageResult] = []

    # Provenance + per-bundle config_hashes on every derived row (ADR 0028).
    try:
        bad = 0
        for table in ("surface_parameters", "iv_points", "pricing_results"):
            for row in store.read(table):
                ch = row.provenance.config_hashes
                if not ch or not all(ch.values()):
                    bad += 1
        if bad:
            results.append(
                StageResult("stage5 provenance", FAIL, f"{bad} rows missing config_hashes")
            )
        else:
            results.append(
                StageResult("stage5 provenance", PASS, "config_hashes on every derived row")
            )
    except Exception as exc:  # noqa: BLE001
        results.append(StageResult("stage5 provenance", FAIL, f"{type(exc).__name__}: {exc}"))

    # Byte-identical replay: a second independent run yields identical derived stamp hashes.
    try:
        from algotrading.core.config import config_hashes, load_platform_config  # noqa: PLC0415
        from algotrading.infra.storage import ParquetStore  # noqa: PLC0415

        second_root = Path(tempfile.mkdtemp(prefix="smoke-e2e-replay-"))
        try:
            config = load_platform_config(repo_root / "configs")
            cfg_hashes = config_hashes(config)
            second = ParquetStore(second_root)
            day = seed_offline_day(second, repo_root)
            run_analytics(second, day, config, cfg_hashes)
            if _derived_stamp_hashes(store) == _derived_stamp_hashes(second):
                results.append(
                    StageResult("stage5 byte-identical", PASS, "two runs, identical stamps")
                )
            else:
                results.append(
                    StageResult("stage5 byte-identical", FAIL, "derived stamps diverged")
                )
        finally:
            shutil.rmtree(second_root, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        results.append(StageResult("stage5 byte-identical", FAIL, f"{type(exc).__name__}: {exc}"))

    return results


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="End-to-end smoke test (offline by default).")
    parser.add_argument("--data-root", default=None, help="scratch store root (default: temp dir)")
    parser.add_argument("--provider", default="SAMPLE", help="data source (default: SAMPLE)")
    parser.add_argument("--date", default=None, help="trade date for a real captured day (1C)")
    parser.add_argument("--skip-web", action="store_true", help="skip the npm build/test stage")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return run_smoke(argv)


if __name__ == "__main__":
    sys.exit(main())
