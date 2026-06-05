"""Pipeline runner: launch a surface build as a tracked, async-safe job.

The ``SAMPLE`` provider drives :func:`orchestration.build_surface` over the committed
``synthetic_known_answer`` chain fixture through the *exact* actor pipeline a live run
uses, and persists the fitted surface into the context's store — so the surfaces/health
endpoints then read real data back. The build is CPU-bound and synchronous, so it runs in
the uvicorn thread pool (``run_in_executor``) and never blocks the event loop; the job's
state is polled through ``GET /api/jobs/{id}``.

Unrunnable providers (e.g. live IBKR) mark the job ``error`` with a typed message rather
than raising past the request boundary.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

import structlog

from config import config_hash, load_config
from connectivity import (
    BrokerTick,
    FakeBrokerSession,
    ManualClock,
    SessionSupervisor,
    client_id_for,
)
from contracts.instrument_key import InstrumentKey
from fixtures.library import ChainFixture, get_fixture
from orchestration import SurfaceJobRequest, build_surface
from universe import ChainSelection

from .context import AppContext
from .providers import SAMPLE_PROVIDER, is_runnable

_LOGGER = structlog.get_logger("frontend.runner")

# The offline fixture the SAMPLE provider replays — the same one the e2e/golden tests use.
_SAMPLE_FIXTURE = "synthetic_known_answer"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class JobStatus:
    """A run job's lifecycle, polled by the web app."""

    job_id: str
    provider: str
    underlying: str
    state: JobState = JobState.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str = ""
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "provider": self.provider,
            "underlying": self.underlying,
            "state": self.state,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "message": self.message,
            "summary": self.summary,
        }


# Process-wide job store (in-memory; a restart drops history, which is acceptable for a BFF).
JOB_STORE: dict[str, JobStatus] = {}

# A small thread pool runs the CPU-bound build off the request thread. Works whether or not
# an asyncio loop is running (Starlette dispatches sync handlers in a threadpool with none).
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="frontend-runner")


def new_job(provider: str, underlying: str) -> JobStatus:
    """Register a queued job and return it."""
    job = JobStatus(job_id=uuid.uuid4().hex[:8], provider=provider, underlying=underlying)
    JOB_STORE[job.job_id] = job
    return job


def _broker_row(instrument: InstrumentKey) -> dict[str, object]:
    """A resolver-ready broker row for one instrument (the shape an adapter emits)."""
    row: dict[str, object] = {
        "conId": instrument.broker_contract_id,
        "symbol": instrument.underlying_symbol,
        "secType": instrument.security_type,
        "exchange": instrument.exchange,
        "currency": instrument.currency,
        "multiplier": instrument.multiplier,
    }
    if instrument.is_option():
        assert instrument.expiry is not None
        row["expiry"] = instrument.expiry.strftime("%Y%m%d")
        row["strike"] = instrument.strike
        row["right"] = instrument.option_right
    return row


def _quote_ticks(
    instrument: InstrumentKey,
    *,
    bid: float,
    ask: float,
    last: float,
    start_sequence: int,
    as_of: datetime,
) -> list[BrokerTick]:
    cid = instrument.broker_contract_id
    return [
        BrokerTick(cid, "bid", bid, sequence=start_sequence, exchange_ts=as_of),
        BrokerTick(cid, "ask", ask, sequence=start_sequence + 1, exchange_ts=as_of),
        BrokerTick(cid, "last", last, sequence=start_sequence + 2, exchange_ts=as_of),
    ]


def _chain_rows_and_script(
    chain: ChainFixture,
) -> tuple[tuple[dict[str, object], ...], list[BrokerTick]]:
    """Turn the fixture into (broker chain rows, a tick script) for a FakeBrokerSession."""
    as_of = chain.as_of
    spot = chain.underlying_spot
    rows = [_broker_row(chain.underlying)]
    script = _quote_ticks(
        chain.underlying, bid=spot - 0.05, ask=spot + 0.05, last=spot, start_sequence=0, as_of=as_of
    )
    seq = 3
    for quote in chain.quotes:
        rows.append(_broker_row(quote.instrument))
        if quote.bid is None or quote.ask is None or quote.last is None:
            continue
        script += _quote_ticks(
            quote.instrument, bid=quote.bid, ask=quote.ask, last=quote.last, start_sequence=seq,
            as_of=as_of,
        )
        seq += 3
    return tuple(rows), script


def _build_sample_surface(ctx: AppContext, job: JobStatus) -> dict[str, Any]:
    """Drive build_surface over the offline fixture and return a job summary."""
    chain = get_fixture(_SAMPLE_FIXTURE)
    as_of = chain.as_of
    trade_date: date = as_of.date()
    rows, script = _chain_rows_and_script(chain)

    clock = ManualClock(start=as_of)
    config = load_config(ctx.configs_dir / "default.toml")
    cfg_hash = config_hash(config)
    symbol = chain.underlying.underlying_symbol
    session = FakeBrokerSession(chains={symbol: rows}, script=script)
    # "collector" is the reserved client-id band for a data-collection session.
    supervisor = SessionSupervisor(session, client_id=client_id_for("collector"), clock=clock)
    supervisor.connect()

    request = SurfaceJobRequest(
        symbol=symbol,
        trade_date=trade_date,
        selection=ChainSelection(),
        market_data_type=3,
        as_of=as_of,
        calc_ts=as_of,
    )
    result = build_surface(
        request=request,
        store=ctx.store,
        config=config,
        config_hash=cfg_hash,
        supervisor=supervisor,
        clock=clock,
        correlation_id=f"api-{job.job_id}",
    )
    params = result.outputs.surface_parameters
    return {
        "underlying": symbol,
        "trade_date": trade_date.isoformat(),
        "n_surface_params": len(params),
        "n_fitted_maturities": result.fitted_maturities,
        "config_hash": cfg_hash,
        "code_version": params[0].provenance.code_version if params else None,
    }


def _run_in_thread(ctx: AppContext, job_id: str) -> None:
    """Synchronous job body executed in a worker thread."""
    job = JOB_STORE[job_id]
    job.state = JobState.RUNNING
    job.started_at = datetime.now(tz=UTC)
    try:
        if job.provider.upper() == SAMPLE_PROVIDER:
            job.message = "Building surface from the offline sample chain…"
            job.summary = _build_sample_surface(ctx, job)
            job.state = JobState.DONE
            job.message = "Pipeline completed successfully"
        else:
            job.state = JobState.ERROR
            job.message = f"provider {job.provider!r} is not runnable in the flat backend"
    except Exception as exc:  # noqa: BLE001 — job boundary: any failure marks the job ERROR and is logged
        job.state = JobState.ERROR
        job.message = str(exc)
        _LOGGER.exception("run job failed", job_id=job_id, provider=job.provider)
    finally:
        job.finished_at = datetime.now(tz=UTC)


def launch_pipeline(ctx: AppContext, job: JobStatus) -> None:
    """Schedule the job on the runner thread pool. Non-blocking."""
    _EXECUTOR.submit(_run_in_thread, ctx, job.job_id)


def run_now(ctx: AppContext, job: JobStatus) -> None:
    """Run the job synchronously in the current thread (used by tests for determinism)."""
    _run_in_thread(ctx, job.job_id)


def is_provider_runnable(provider: str) -> bool:
    """Re-exported guard so the router validates without importing providers directly."""
    return is_runnable(provider)
