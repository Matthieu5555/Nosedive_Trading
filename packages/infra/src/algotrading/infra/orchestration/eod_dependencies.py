"""The runner's injectable dependency bundle and its production default wiring.

:class:`RunnerDeps` is every external dependency one fire needs; :func:`build_default_deps`
resolves the production values from config + environment (the live daily fire). A test passes a
fully-faked :class:`RunnerDeps` so the path runs with no broker, no git subprocess, no wall clock.
"""

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
    """Every external dependency one fire needs, injected so the path is fully testable.

    Production defaults are resolved lazily by :func:`build_default_deps` (which reads the
    config bundles and probes git for the code identity); a test passes fakes for all of them
    so ``main`` runs with no broker, no git subprocess, and no wall clock. ``code_identity`` is
    a *value* (already resolved at the entrypoint, never deeper in compute — ADR 0028).
    """

    store: ParquetStore
    config: PlatformConfig
    registry: IndexRegistry
    resolver: SessionResolver
    run_repository: RunRegistry
    stages_builder: StagesBuilder
    clock: Clock
    code_identity: str
    environment: str = "production"


# --------------------------------------------------------------------------- #
# Production wiring — only reached when no deps are injected (live daily fire). #
# --------------------------------------------------------------------------- #

_REPO_ROOT = repo_root()
_CONFIGS_DIR = _REPO_ROOT / "configs"


@dataclass(frozen=True, slots=True)
class _DefaultEnv:
    """The few environment values the production wiring resolves (kept tiny, injectable)."""

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
    """Build the production :class:`RunnerDeps` from config + environment (the live fire).

    Loads the economic config bundles, parses the typed index registry, builds the calendar
    resolver, opens the run repository, resolves the code identity from git *once* at the
    entrypoint, and uses :func:`default_stages_builder`. Reached only when ``main`` is called
    with no injected deps — every test injects its own, so this never runs under the gate.

    ``basket_source`` is the 1C close-capture seam: ``None`` (the default) leaves the runner on
    :func:`_empty_basket_source` (the clean no-capture day, exit 0); a credentialed caller passes
    a live ``collect_live``-backed source (built in the broker leaf above this layer, which cannot
    be imported here) and it is threaded into :func:`default_stages_builder` so a real fire
    captures and persists the grid. The selection between the two lives in the entrypoint shim
    that *can* see both layers; this function only carries whichever source it is handed.
    """
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
        # Bound the resolver's calendars to the fire's as-of (the injected clock's day) so the
        # session/coverage window is deterministic and replayable, never wall-clock-dependent
        # inside exchange_calendars (M6/1J).
        resolver=CalendarResolver(registry, as_of=clock),
        run_repository=RunRegistry(env.runs_db.parent),
        stages_builder=stages_builder,
        clock=clock,
        code_identity=_git_code_identity(),
        environment=env.environment,
    )
