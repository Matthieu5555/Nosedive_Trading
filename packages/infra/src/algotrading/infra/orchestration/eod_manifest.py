from __future__ import annotations

from algotrading.core.config import config_hashes, config_snapshot
from algotrading.core.manifest import Manifest
from algotrading.core.provenance import code_version
from algotrading.infra.storage import RunRecord

from .eod_dependencies import RunnerDeps
from .eod_planning import EOD_JOB_NAME, EodRunPlan

_INFRA_DISTRIBUTION = "algotrading-infra"


def _record_manifest(deps: RunnerDeps, plan: EodRunPlan, *, status: str) -> None:
    started = deps.clock.now()
    manifest = Manifest(
        run_id=plan.correlation_id,
        environment=deps.environment,
        code_version=code_version(_INFRA_DISTRIBUTION),
        code_identity=deps.code_identity,
        config_hashes=config_hashes(deps.config),
        config_snapshot=config_snapshot(deps.config),
        input_partitions={},
        output_partitions={
            fired.entry.symbol: plan.trade_date.isoformat() for fired in plan.fired
        },
        status=status,
        correlation_id=plan.correlation_id,
    )
    deps.run_repository.record(
        RunRecord(
            manifest=manifest,
            job=EOD_JOB_NAME,
            started_at=started,
            ended_at=deps.clock.now(),
        )
    )
