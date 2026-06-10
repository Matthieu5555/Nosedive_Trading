"""Shared helpers for the marimo app-notebooks under ``notebooks/apps/``.

This is **not** a marimo notebook -- it is a plain importable module the apps load
so they stay DRY and honor the one notebook discipline: a notebook only *imports and
calls* the tested engines. Every analytic below is a call into the library, never a
formula re-implemented here.

The apps put this directory on ``sys.path`` and ``import _shared`` (see each app's
bootstrap cell). Kept dependency-light: stdlib + algotrading + plotly only.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from algotrading.core.config import PlatformConfig, config_hash, load_platform_config
from algotrading.infra.actor import ActorOutputs
from algotrading.infra.actor.driver import PROJECTION_AXES_VERSION, run_analytics
from algotrading.infra.contracts import (
    InstrumentMaster,
    RawMarketEvent,
    content_event_id,
)
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.orchestration.reconstruction import reconstruct_day
from algotrading.infra.storage import ParquetStore, events_from_json
from algotrading.infra.surfaces.projection import ProjectionConfig
from algotrading.infra.universe import parse_instrument_key
from algotrading.infra.universe.contracts import Underlying

# Quote fields the actor consumes to rebuild snapshots (bid/ask/last -> reference
# spot). Any broker-supplied Greek/IV column is intentionally not replayed into the
# compute: the engine reconstructs forward, IV and Greeks from the quotes alone.
_QUOTE_FIELDS = ("bid", "ask", "last")

# The pipeline emits structured QC logs to stderr; raise the floor so an app's own
# output is what shows. Not a change to the engine.
logging.getLogger("algotrading").setLevel(logging.ERROR)


# --------------------------------------------------------------------------- paths


def repo_root() -> Path:
    """Walk up from cwd to the monorepo root (``pyproject.toml`` + ``packages/``)."""
    for p in (Path.cwd(), *Path.cwd().parents):
        if (p / "pyproject.toml").exists() and (p / "packages").is_dir():
            return p
    raise FileNotFoundError("repo root not found (pyproject.toml + packages/)")


def committed_samples() -> dict[str, dict[str, object]]:
    """Human-labelled committed real samples, for an app dropdown.

    Offline replay only -- no broker, no network, no token. Each entry carries the
    sample path plus the underlying/exchange used to label the demo config.
    """
    root = repo_root()
    return {
        "IBKR ASML - EUREX, 4 maturities": {
            "path": root / "packages/infra-ibkr/samples/asml_real_2026-06-05.json",
            "underlying": "ASML",
            "exchange": "EUREX",
        },
        "Saxo ASML - 1 maturity": {
            "path": root / "packages/infra-saxo/samples/asml_real_2026-06-04.json",
            "underlying": "ASML",
            "exchange": "SAXO",
        },
    }


# -------------------------------------------------------------------------- config


def demo_platform_config(underlying: str, exchange: str) -> PlatformConfig:
    """The real platform config from ``configs/``, relaxed only for offline replay.

    A committed sample is, by construction, older than now and may carry a thinner
    chain than the live floor, so the staleness ceiling, chain-count floor and spread
    ceiling are widened for replay. Every *economic* parameter (SVI bounds, forward,
    monetization, scenario shocks) is the real configured value -- nothing invented
    in Python, per the project's config standard.
    """
    base = load_platform_config(repo_root() / "configs")
    qc = base.qc_threshold.model_copy(
        update={
            "max_quote_age_seconds": 86_400.0,
            "min_chain_count": 1,
            "max_spread_pct": 0.6,
        }
    )
    universe = base.universe.model_copy(
        update={"underlyings": (underlying,), "exchange": exchange}
    )
    return base.model_copy(update={"qc_threshold": qc, "universe": universe})


# --------------------------------------------------------------------- replay path


def _instrument_key_of(colon_key: str, broker_id: str) -> InstrumentKey:
    """Relabel a sample's canonical colon key into the contracts pipe-form key.

    The committed samples carry the universe colon-form key (``OPT:ASML:...``); the
    actor matches snapshots against the contracts ``InstrumentKey`` and reads
    strike/expiry/right off the master. Pure relabelling of one identity into the
    other -- no analytics, no market data invented.
    """
    domain = parse_instrument_key(colon_key)
    if isinstance(domain, Underlying):
        return InstrumentKey(
            underlying_symbol=domain.symbol,
            security_type=domain.security_type,
            exchange=domain.exchange,
            currency=domain.currency,
            multiplier=1.0,
            broker_contract_id=broker_id,
        )
    return InstrumentKey(
        underlying_symbol=domain.symbol,
        security_type=domain.security_type,
        exchange=domain.exchange,
        currency=domain.currency,
        multiplier=float(domain.multiplier),
        broker_contract_id=broker_id,
        expiry=domain.expiry,
        strike=float(domain.strike),
        option_right=domain.right.value,
    )


@dataclass(frozen=True, slots=True)
class _ReplayInputs:
    """The actor's inputs rebuilt from a committed sample -- the shared half of replay."""

    config: PlatformConfig
    events: list[RawMarketEvent]
    instruments: list[InstrumentKey]
    masters: list[InstrumentMaster]
    as_of: datetime
    trade_date: date
    config_hashes: Mapping[str, str]


def _replay_inputs(
    sample_path: str | Path, *, underlying: str, exchange: str
) -> _ReplayInputs:
    """Rebuild the actor's inputs from a committed EAV sample (the half both replays share).

    Reads the sample (``events_from_json``), relabels each instrument key into its contracts
    form, replays only the ``bid/ask/last`` quote fields into ``RawMarketEvent``s (Greeks/IV
    are reconstructed, never replayed), and builds the day's masters and config hash. Pure
    relabelling + filtering -- no analytics, no market data invented.
    """
    config = demo_platform_config(underlying, exchange)
    storage_events = events_from_json(Path(sample_path).read_text(encoding="utf-8"))
    as_of = max(e.receipt_ts for e in storage_events)
    trade_date = as_of.date()

    broker_id_by_key: dict[str, str] = {}
    for e in storage_events:
        broker_id_by_key.setdefault(
            e.instrument_key, e.contract_id_broker or e.instrument_key
        )
    instrument_by_key = {
        colon: _instrument_key_of(colon, broker_id)
        for colon, broker_id in broker_id_by_key.items()
    }
    canonical = {colon: ik.canonical() for colon, ik in instrument_by_key.items()}

    contract_events: list[RawMarketEvent] = []
    sequence: dict[tuple[str, str], int] = {}
    for e in storage_events:
        if e.field_value is None or e.field_name not in _QUOTE_FIELDS:
            continue
        key = canonical[e.instrument_key]
        seq = sequence.get((key, e.field_name), 0)
        sequence[(key, e.field_name)] = seq + 1
        canonical_ts = e.exchange_ts or e.receipt_ts
        contract_events.append(
            RawMarketEvent(
                session_id=e.collector_session_id,
                event_id=content_event_id(key, e.field_name, seq),
                instrument_key=key,
                exchange_ts=canonical_ts,
                receipt_ts=e.receipt_ts,
                canonical_ts=canonical_ts,
                field_name=e.field_name,
                value=float(e.field_value),
                trade_date=trade_date,
                underlying=e.underlying,
            )
        )

    instruments = list(instrument_by_key.values())
    masters = [
        InstrumentMaster(
            instrument_key=ik.canonical(),
            as_of_date=trade_date,
            instrument=ik,
            raw_broker_payload="{}",
        )
        for ik in instruments
    ]
    return _ReplayInputs(
        config=config,
        events=contract_events,
        instruments=instruments,
        masters=masters,
        as_of=as_of,
        trade_date=trade_date,
        config_hashes={"cfg": config_hash(config)},
    )


def replay_sample(
    sample_path: str | Path,
    *,
    underlying: str,
    exchange: str,
) -> tuple[ActorOutputs, datetime, list[InstrumentMaster], PlatformConfig]:
    """Replay a committed real sample through the one actor pipeline, fully offline.

    Reads the EAV sample (``events_from_json``), relabels each instrument key to its
    contracts form, builds the day's masters, seeds an ephemeral ``ParquetStore`` raw
    layer, and runs ``reconstruct_day`` -- the *same* compute path production runs
    live (ADR 0007). No broker, no network, no token.

    Returns ``(outputs, as_of, masters, config)`` where ``outputs`` is the typed
    ``ActorOutputs`` (snapshots, forwards, iv_points, surface_parameters,
    surface_grid, pricings, risk_aggregates, scenarios, projected_analytics).

    Note: ``reconstruct_day`` is the replay-equality path and leaves the *provider* unset,
    so ``outputs.projected_analytics`` (the provider-partitioned tenor x delta-band grid)
    is empty by design. Use :func:`replay_projected_grid` when you need that grid populated.
    """
    inp = _replay_inputs(sample_path, underlying=underlying, exchange=exchange)
    with tempfile.TemporaryDirectory() as tmp:
        store = ParquetStore(Path(tmp) / "store")
        store.write("raw_market_events", inp.events)
        day = reconstruct_day(
            store,
            inp.trade_date,
            [],
            instruments=inp.instruments,
            masters=inp.masters,
            config=inp.config,
            config_hashes=inp.config_hashes,
            as_of=inp.as_of,
            calc_ts=inp.as_of,
            persist=False,
        )
    assert day.outputs is not None, f"replay produced no outputs: {day.reason!r}"
    return day.outputs, inp.as_of, inp.masters, inp.config


def replay_projected_grid(
    sample_path: str | Path,
    *,
    underlying: str,
    exchange: str,
    provider: str,
    clamp_to_span: bool = False,
) -> tuple[ActorOutputs, datetime, PlatformConfig]:
    """Replay a committed sample with a ``provider``, so the dollar-Greek grid is populated.

    Same offline compute as :func:`replay_sample` (the tested actor, no broker/network), but
    calls :func:`actor.run_analytics` directly with a ``provider`` set -- which is what the
    live EOD close-capture path does. That ``provider`` is the gate on
    :attr:`ActorOutputs.projected_analytics`: the pinned tenor x delta-band grid is
    provider-partitioned, so the provider-less :func:`replay_sample` leaves it empty by
    design while this fills it. Nothing here re-implements analytics -- it only selects the
    provider-stamped code path and the projection axes.

    ``clamp_to_span`` mirrors :class:`ProjectionConfig`: off (default, the production axis)
    emits only pinned tenors inside the fitted maturity span and labels the rest as gaps;
    on, it clamps each pinned tenor to the span edge -- denser, but an *extrapolation* a
    thin sample chain should show as such. Returns ``(outputs, as_of, config)``.
    """
    inp = _replay_inputs(sample_path, underlying=underlying, exchange=exchange)
    projection = ProjectionConfig(
        version=PROJECTION_AXES_VERSION, clamp_to_span=clamp_to_span
    )
    outputs = run_analytics(
        inp.events,
        [],
        instruments=inp.instruments,
        masters=inp.masters,
        config=inp.config,
        config_hashes=inp.config_hashes,
        as_of=inp.as_of,
        calc_ts=inp.as_of,
        provider=provider,
        projection=projection,
    )
    return outputs, inp.as_of, inp.config


def maturity_years(expiry: date, as_of_date: date) -> float:
    """ACT/365 year fraction -- the one day-count the actor solves maturity under."""
    return (expiry - as_of_date).days / 365.0


# ------------------------------------------------------------------- plotly theme

# A compact, consistent Plotly theme lifted from the original pipeline notebooks so
# every app reads the same. Call ``apply_plotly_theme()`` once in an app's setup cell.

C = {
    "blue": "#2563EB",
    "teal": "#0D9488",
    "violet": "#7C3AED",
    "amber": "#D97706",
    "red": "#DC2626",
    "green": "#16A34A",
    "indigo": "#4F46E5",
    "slate900": "#0F172A",
    "slate600": "#475569",
    "slate400": "#94A3B8",
    "slate100": "#F1F5F9",
    "white": "#FFFFFF",
}
DISCRETE = [C["blue"], C["teal"], C["violet"], C["amber"], C["indigo"], "#0EA5E9", "#F59E0B"]
FONT = "Inter, IBM Plex Sans, -apple-system, sans-serif"
SURFACE_COLORSCALE = [
    [0.00, "#1E3A5F"],
    [0.25, "#2563EB"],
    [0.50, "#0D9488"],
    [0.75, "#D97706"],
    [1.00, "#DC2626"],
]
SURFACE_CAMERA = {"eye": {"x": 1.6, "y": -1.6, "z": 0.9}, "up": {"x": 0, "y": 0, "z": 1}}
SURFACE_ASPECT = {"x": 1.5, "y": 1.2, "z": 0.7}


def apply_plotly_theme() -> str:
    """Register and select the ``algotrading`` Plotly template; return its name."""
    import plotly.graph_objects as go
    import plotly.io as pio

    axis = {
        "showgrid": True,
        "gridcolor": C["slate400"],
        "gridwidth": 0.5,
        "zeroline": False,
        "linecolor": C["slate400"],
        "linewidth": 1,
        "ticks": "outside",
        "tickcolor": C["slate400"],
        "tickfont": {"size": 11},
        "title_font": {"size": 12, "color": C["slate600"]},
    }
    tmpl = go.layout.Template()
    tmpl.layout = go.Layout(
        font={"family": FONT, "size": 12, "color": C["slate600"]},
        title_font={"family": FONT, "size": 15, "color": C["slate900"]},
        paper_bgcolor=C["white"],
        plot_bgcolor=C["white"],
        colorway=DISCRETE,
        xaxis=axis,
        yaxis=axis,
        legend={
            "bgcolor": "rgba(255,255,255,0.85)",
            "bordercolor": C["slate400"],
            "borderwidth": 1,
            "font": {"size": 11},
            "tracegroupgap": 4,
        },
        margin={"l": 64, "r": 24, "t": 56, "b": 56},
        hoverlabel={
            "bgcolor": C["slate900"],
            "font": {"color": C["white"], "size": 12, "family": FONT},
            "bordercolor": C["slate900"],
        },
        hovermode="closest",
    )
    pio.templates["algotrading"] = tmpl
    pio.templates.default = "plotly_white+algotrading"
    return "plotly_white+algotrading"
