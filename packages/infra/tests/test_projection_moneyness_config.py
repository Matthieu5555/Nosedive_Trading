"""core-projection-moneyness-grid (ADR 0028) — the persisted surface grid is sampled on the
log-moneyness grid that lives in ``SurfaceConfig.moneyness_buckets``, not on a ``.py`` literal.

These pin the *wiring*: the projection chain reads the configured grid (resolved at the single
choke point ``run_analytics_with_qc``), an explicit per-run grid still overrides it, and the
shipped default reproduces the canonical five buckets. The oracle is independent — the configured
bucket tuple itself — because ``surfaces.surface_grid_cells`` emits exactly one persisted cell per
bucket, so the distinct ``moneyness_bucket`` values of the output must equal the grid that drove it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    SurfaceConfig,
    UniverseConfig,
)
from algotrading.infra.actor import run_analytics
from algotrading.infra.contracts import InstrumentMaster
from algotrading.infra.contracts.instrument_key import InstrumentKey
from fixtures.events import quote_events
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, ChainFixture, get_fixture

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
CONFIG_HASH = {"cfg": "cfg-hash-moneyness"}

# The canonical shipped grid (configs/pricing.yaml) — the byte-identical default.
DEFAULT_GRID = (-0.2, -0.1, 0.0, 0.1, 0.2)
# A different, still-valid (strictly increasing + symmetric about 0) grid for the override oracle.
CUSTOM_GRID = (-0.15, 0.0, 0.15)


def _config(surface: SurfaceConfig) -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        surface=surface,
        forward=FORWARD_CONFIG,
        scenario=ScenarioConfig(
            version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05, -0.05)
        ),
    )


def _master(instrument: InstrumentKey) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=AS_OF.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _inputs(chain: ChainFixture):  # type: ignore[no-untyped-def]
    spot = chain.underlying_spot
    events = list(
        quote_events(
            chain.underlying, bid=spot - 0.05, ask=spot + 0.05, last=spot, ts=AS_OF,
            session_id=chain.underlying.canonical(),
        )
    )
    instruments = [chain.underlying]
    masters = [_master(chain.underlying)]
    for quote in chain.quotes:
        events += list(
            quote_events(
                quote.instrument, bid=quote.bid, ask=quote.ask, last=quote.last, ts=AS_OF,
                session_id=quote.instrument.canonical(),
            )
        )
        instruments.append(quote.instrument)
        masters.append(_master(quote.instrument))
    return events, instruments, masters


def _grid_buckets(surface_override: SurfaceConfig, **run_kwargs) -> set[float]:  # type: ignore[no-untyped-def]
    """Run the actor over the known-answer chain and return the distinct persisted grid buckets."""
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _inputs(chain)
    outputs = run_analytics(
        events, [], instruments=instruments, masters=masters,
        config=_config(surface_override), config_hashes=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS,
        **run_kwargs,
    )
    assert outputs.surface_grid, "the known-answer chain must produce a non-empty surface grid"
    return {cell.moneyness_bucket for cell in outputs.surface_grid}


def test_persisted_surface_grid_uses_the_configured_moneyness_grid() -> None:
    # A config carrying a custom grid drives the persisted cells: the distinct buckets are exactly it.
    surface = SURFACE_CONFIG.model_copy(update={"moneyness_buckets": CUSTOM_GRID})
    assert _grid_buckets(surface) == set(CUSTOM_GRID)


def test_shipped_default_reproduces_the_canonical_five_buckets() -> None:
    # The default SurfaceConfig (no override) reproduces the canonical grid byte-for-byte — and the
    # two configs disagree, which is what proves config (not a literal) is what shaped the output.
    assert _grid_buckets(SURFACE_CONFIG) == set(DEFAULT_GRID)
    assert set(DEFAULT_GRID) != set(CUSTOM_GRID)


def test_explicit_per_run_grid_overrides_the_configured_grid() -> None:
    # An explicit moneyness_buckets= on the run (a test/notebook caller) wins over config: even with
    # the default 5-bucket config, the persisted grid collapses to the one explicit ATM bucket.
    assert _grid_buckets(SURFACE_CONFIG, moneyness_buckets=(0.0,)) == {0.0}
