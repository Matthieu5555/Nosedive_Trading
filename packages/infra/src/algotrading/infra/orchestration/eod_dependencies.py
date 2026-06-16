from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

from algotrading.core.config import PlatformConfig
from algotrading.core.config.loader import load_platform_config
from algotrading.core.paths import data_root as core_data_root
from algotrading.core.paths import repo_root
from algotrading.core.provenance import code_identity as _git_code_identity
from algotrading.infra.connectivity import Clock, SystemClock
from algotrading.infra.storage import ParquetStore, RunRegistry
from algotrading.infra.universe import (
    CalendarResolver,
    IndexRegistry,
    index_registry_from_config,
)

from .eod_planning import SessionResolver
from .eod_stages import BasketSource, StagesBuilder, default_stages_builder


@dataclass(frozen=True, slots=True)
class RunnerDeps:

    store: ParquetStore
    config: PlatformConfig
    registry: IndexRegistry
    resolver: SessionResolver
    run_repository: RunRegistry
    stages_builder: StagesBuilder
    clock: Clock
    code_identity: str
    environment: str = "production"


_REPO_ROOT = repo_root()
_CONFIGS_DIR = _REPO_ROOT / "configs"


@dataclass(frozen=True, slots=True)
class _DefaultEnv:

    data_root: Path
    runs_db: Path
    configs_dir: Path = _CONFIGS_DIR
    environment: str = "production"


def _default_env() -> _DefaultEnv:
    import os

    data_root = core_data_root()
    runs_db = Path(os.environ.get("ALGOTRADING_RUNS_DB", str(data_root / "runs.db")))
    return _DefaultEnv(
        data_root=data_root,
        runs_db=runs_db,
        environment=os.environ.get("ALGOTRADING_ENV", "production"),
    )


def build_default_deps(*, basket_source: BasketSource | None = None) -> RunnerDeps:
    env = _default_env()
    config = load_platform_config(env.configs_dir)
    registry = index_registry_from_config(config)
    clock = SystemClock()
    stages_builder: StagesBuilder = (
        default_stages_builder
        if basket_source is None
        else functools.partial(default_stages_builder, basket_source=basket_source)
    )
    return RunnerDeps(
        store=ParquetStore(env.data_root),
        config=config,
        registry=registry,
        resolver=CalendarResolver(registry, as_of=clock),
        run_repository=RunRegistry(env.runs_db.parent),
        stages_builder=stages_builder,
        clock=clock,
        code_identity=_git_code_identity(),
        environment=env.environment,
    )
