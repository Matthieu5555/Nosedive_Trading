from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import date

import structlog
from algotrading.core.config import config_hashes
from algotrading.infra.qc import ESCALATION_PAGE
from algotrading.infra.storage import RunStatus

from .eod_dependencies import RunnerDeps, build_default_deps
from .eod_manifest import _record_manifest
from .eod_planning import (
    EOD_JOB_NAME,
    EodRunError,
    EodRunPlan,
    FiredIndex,
    SessionResolver,
    plan_fire,
)
from .eod_stages import (
    BasketSource as BasketSource,
)
from .eod_stages import (
    StagesBuilder,
    analytics_qc_results,
    default_stages_builder,
    persist_triage,
)
from .pipeline import EodResult, run_end_of_day

_LOGGER = structlog.get_logger("orchestration.eod_run")


def run_fire(
    deps: RunnerDeps,
    *,
    trade_date: date | None = None,
    calendar: str | None = None,
    index: str | None = None,
    correlation_id: str | None = None,
) -> EodResult | None:
    plan = plan_fire(
        deps,
        trade_date=trade_date,
        calendar=calendar,
        index=index,
        correlation_id=correlation_id,
    )
    log = _LOGGER.bind(
        correlation_id=plan.correlation_id,
        job=EOD_JOB_NAME,
        trade_date=plan.trade_date.isoformat(),
    )
    if plan.is_noop:
        log.info("orchestration.eod_run.noop", reason="no index in session for this fire")
        _record_manifest(deps, plan, status=RunStatus.OK)
        return None

    log.info(
        "orchestration.eod_run.start",
        indices=[f.entry.symbol for f in plan.fired],
        as_of={f.entry.symbol: f.as_of.isoformat() for f in plan.fired},
    )
    stages = deps.stages_builder(
        deps.store,
        deps.config,
        config_hashes(deps.config),
        deps.clock,
        plan.correlation_id,
        plan.fired,
    )
    try:
        result = run_end_of_day(
            deps.store,
            trade_date=plan.trade_date,
            correlation_id=plan.correlation_id,
            clock=deps.clock,
            stages=stages,
        )
    except Exception:
        _record_manifest(deps, plan, status=RunStatus.FAILED)
        log.error("orchestration.eod_run.failed")
        raise
    # A critical (page) QC escalation is a failed fire: every stage ran and persisted, but the
    # result is not trustworthy, so the manifest is FAILED (not OK) and `main` maps it to a
    # non-zero exit that engages systemd OnFailure= (the close-capture alert). A notice/clean
    # report stays OK — it belongs in the triage queue, not a page. The result is returned either
    # way (the data is on disk); the page is reported here, never an abort mid-pipeline.
    paged = result.escalation == ESCALATION_PAGE
    _record_manifest(deps, plan, status=RunStatus.FAILED if paged else RunStatus.OK)
    if paged:
        log.error("orchestration.eod_run.qc_escalated_to_page", escalation=result.escalation)
    else:
        log.info("orchestration.eod_run.done", ran=result.ran)
    return result


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="eod_run",
        description="Daily close-capture: fire run_end_of_day once per market day (WS 1G).",
    )
    parser.add_argument(
        "--trade-date",
        type=date.fromisoformat,
        default=None,
        help="ISO date to capture (default: the clock's current market day). "
        "A future date is rejected (no look-ahead).",
    )
    parser.add_argument(
        "--calendar",
        default=None,
        help="scope the fire to one exchange-calendar code (e.g. XEUR, XNYS). "
        "Default: all enabled indices.",
    )
    parser.add_argument(
        "--index",
        default=None,
        help="scope the fire to a single index symbol (e.g. SX5E). Default: all enabled.",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    deps: RunnerDeps | None = None,
    deps_factory: Callable[[], RunnerDeps] | None = None,
) -> int:
    args = _parse_args(argv)
    if deps is None:
        deps = (deps_factory or build_default_deps)()
    try:
        result = run_fire(
            deps,
            trade_date=args.trade_date,
            calendar=args.calendar,
            index=args.index,
        )
    except EodRunError as exc:
        _LOGGER.error("orchestration.eod_run.bad_request", reason=str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001 — surface any stage failure as a non-zero exit
        _LOGGER.error("orchestration.eod_run.error", error=str(exc))
        return 1
    # A critical (page) QC escalation completes the pipeline (data persisted) but is not a clean
    # close: exit non-zero so Restart=on-failure / OnFailure= engage and the operator is alerted,
    # instead of a silent exit 0 (the gap the 2026-06-15 ingestion audit found). A no-op fire
    # (result is None) and a notice/clean report exit 0.
    if result is not None and result.escalation == ESCALATION_PAGE:
        _LOGGER.error("orchestration.eod_run.qc_escalated_to_page", escalation=result.escalation)
        return 1
    return 0


__all__ = [
    "EOD_JOB_NAME",
    "EodRunError",
    "EodRunPlan",
    "FiredIndex",
    "RunnerDeps",
    "SessionResolver",
    "StagesBuilder",
    "analytics_qc_results",
    "build_default_deps",
    "default_stages_builder",
    "main",
    "persist_triage",
    "plan_fire",
    "run_fire",
]
