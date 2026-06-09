"""The per-run lineage manifest freeze — make a scheduled fire reproducible from its record.

:func:`_record_manifest` builds and persists the manifest (resolved config snapshot + per-bundle
config hashes + code identity) keyed by the fire's correlation id, for both a clean and a failed
fire. Importing :class:`RunnerDeps` at runtime is safe: there is no reverse edge (the dependencies
module never imports this one).
"""

from __future__ import annotations

from algotrading.core.config import config_hashes, config_snapshot
from algotrading.core.manifest import Manifest
from algotrading.core.provenance import code_version
from algotrading.infra.storage import RunRecord

from .eod_dependencies import RunnerDeps
from .eod_planning import EOD_JOB_NAME, EodRunPlan

# The distribution whose installed version is stamped on the manifest (best-effort).
_INFRA_DISTRIBUTION = "algotrading-infra"


def _record_manifest(deps: RunnerDeps, plan: EodRunPlan, *, status: str) -> None:
    """Freeze and persist this fire's per-run manifest (config snapshot + hashes + code id).

    The scheduled run must be reproducible *from its manifest*, not merely traceable through the
    JSONL ledger (ADR 0028 / C7): the manifest carries the fully-resolved config snapshot, the
    per-bundle ``config_hashes``, and the code identity (commit SHA + dirty flag). Keyed by the
    fire's ``correlation_id`` so a re-fire/restart overwrites its record rather than duplicating
    it. Recorded for both a clean fire and a failed one, so a failure is reproducible too.
    """
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
