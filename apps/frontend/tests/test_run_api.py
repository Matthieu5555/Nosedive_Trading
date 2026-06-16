from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from algotrading.frontend import runner
from algotrading.frontend.context import AppContext
from algotrading.infra.collectors import BrokerTick, RawCollector, next_sequence, replay_day
from algotrading.infra.connectivity import ManualClock
from algotrading.infra.contracts import InstrumentKey, InstrumentMaster
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient
from fixtures.library import get_fixture

_REPO_ROOT = Path(__file__).parents[3]


def test_liveness_is_ok(infra_client: TestClient) -> None:
    response = infra_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_providers_lists_sample_as_ready(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/providers").json()
    by_name = {p["provider"]: p for p in payload["providers"]}
    assert by_name["SAMPLE"]["status"] == "ready"
    assert by_name["IBKR"]["status"] == "unavailable"
    assert "SAXO" not in by_name
    assert "DERIBIT" not in by_name


def test_run_rejects_unknown_provider(infra_client: TestClient) -> None:
    response = infra_client.post("/api/run", json={"provider": "NOPE"})
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_provider"


def test_run_rejects_unavailable_provider(infra_client: TestClient) -> None:
    response = infra_client.post("/api/run", json={"provider": "IBKR"})
    assert response.status_code == 409
    assert response.json()["error"] == "provider_unavailable"
    assert "note" in response.json()


def test_run_launch_returns_202_queued_job(infra_client: TestClient) -> None:
    response = infra_client.post("/api/run", json={"provider": "SAMPLE"})
    assert response.status_code == 202
    job = response.json()
    assert job["provider"] == "SAMPLE"
    assert infra_client.get(f"/api/jobs/{job['job_id']}").status_code == 200


def test_get_job_returns_status(infra_client: TestClient) -> None:
    response = infra_client.post("/api/run", json={"provider": "SAMPLE"})
    job_id = response.json()["job_id"]
    status = infra_client.get(f"/api/jobs/{job_id}").json()
    assert status["job_id"] == job_id
    assert status["provider"] == "SAMPLE"


def test_get_job_unknown_returns_404(infra_client: TestClient) -> None:
    response = infra_client.get("/api/jobs/doesnotexist")
    assert response.status_code == 404
    assert response.json()["error"] == "job_not_found"


def test_list_jobs_includes_launched_job(infra_client: TestClient) -> None:
    infra_client.post("/api/run", json={"provider": "SAMPLE"})
    jobs = infra_client.get("/api/jobs").json()["jobs"]
    assert len(jobs) >= 1


def test_run_underlyings_includes_context_default(infra_client: TestClient) -> None:
    payload = infra_client.get("/api/run/underlyings").json()
    assert "SX5E" in payload["underlyings"]


def test_job_stores_are_per_app_not_module_global(ctx: AppContext, tmp_path: Path) -> None:
    other_root = tmp_path / "other-data"
    other_ctx = AppContext(
        store_root=other_root,
        configs_dir=tmp_path / "other-configs",
        store=ParquetStore(other_root),
    )
    from algotrading.frontend.app import create_app

    with TestClient(create_app(ctx)) as first, TestClient(create_app(other_ctx)) as second:
        job_id = first.post("/api/run", json={"provider": "SAMPLE"}).json()["job_id"]
        assert first.get(f"/api/jobs/{job_id}").status_code == 200
        assert second.get(f"/api/jobs/{job_id}").status_code == 404
        assert second.get("/api/jobs").json()["jobs"] == []


def test_runner_pool_shuts_down_with_the_app_lifespan(ctx: AppContext) -> None:
    from algotrading.frontend.app import create_app

    app = create_app(ctx)
    with TestClient(app):
        pass
    pipeline = app.state.runner
    job = pipeline.new_job("SAMPLE", "AAPL")
    with pytest.raises(RuntimeError):
        pipeline.launch_pipeline(ctx, job)


class _ScriptedAdapter:

    def __init__(self, ticks: list[BrokerTick]) -> None:
        self._ticks = ticks
        self._tick_cb: object = None

    def subscribe(self, instrument_keys: object) -> None: ...
    def set_tick_callback(self, callback: object) -> None:
        self._tick_cb = callback

    def set_fault_callback(self, callback: object) -> None: ...
    def unsubscribe_all(self) -> None: ...

    def pump(self, _collector: RawCollector) -> None:
        for tick in self._ticks:
            self._tick_cb(tick)  # type: ignore[operator]


def _master(instrument: InstrumentKey, as_of_date: object) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=as_of_date,
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _seed_committed_day(store: ParquetStore, configs_dir: Path) -> object:
    chain = get_fixture("synthetic_known_answer")
    spot = chain.underlying_spot
    counters: dict[tuple[str, str], int] = {}
    ticks: list[BrokerTick] = []
    masters: list[InstrumentMaster] = [_master(chain.underlying, chain.as_of.date())]

    def _add(instrument: InstrumentKey, bid: float, ask: float, last: float) -> None:
        key = instrument.canonical()
        for field_name, value in (("bid", bid), ("ask", ask), ("last", last)):
            ticks.append(
                BrokerTick(
                    instrument_key=key,
                    field_name=field_name,
                    value=value,
                    underlying=instrument.underlying_symbol,
                    sequence=next_sequence(counters, key, field_name),
                    exchange_ts=chain.as_of,
                )
            )

    _add(chain.underlying, spot - 0.05, spot + 0.05, spot)
    for quote in chain.quotes:
        _add(quote.instrument, quote.bid, quote.ask, quote.last)
        masters.append(_master(quote.instrument, chain.as_of.date()))

    keys = [m.instrument.canonical() for m in masters]
    adapter = _ScriptedAdapter(ticks)
    collector = RawCollector(
        store=store,
        adapter=adapter,
        session_id="seed",
        trade_date=chain.as_of.date(),
        clock=ManualClock(start=chain.as_of),
        subscribed_keys=keys,
    )
    collector.start(keys)
    adapter.pump(collector)
    collector.close()
    store.write("instrument_master", masters)

    configs_dir.mkdir(parents=True, exist_ok=True)
    for bundle in _REPO_ROOT.glob("configs/*.yaml"):
        shutil.copy(bundle, configs_dir / bundle.name)
    return chain.as_of.date()


@pytest.fixture
def seeded_ctx(tmp_path: Path) -> tuple[AppContext, object]:
    store_root = tmp_path / "data"
    configs_dir = tmp_path / "configs"
    store = ParquetStore(store_root)
    trade_date = _seed_committed_day(store, configs_dir)
    ctx = AppContext(store_root=store_root, configs_dir=configs_dir, store=store)
    return ctx, trade_date


def test_sample_run_builds_a_surface_and_leaves_the_source_store_untouched(
    seeded_ctx: tuple[AppContext, object],
) -> None:
    ctx, trade_date = seeded_ctx
    raw_before = len(replay_day(ctx.store, trade_date, underlying="AAPL"))

    pipeline = runner.PipelineRunner()
    job = pipeline.new_job("SAMPLE", "AAPL")
    pipeline.run_now(ctx, job)

    assert job.state == runner.JobState.DONE, job.message
    assert job.finished_at is not None
    summary = job.summary
    assert summary["underlying"] == "AAPL"
    assert summary["trade_date"] == trade_date.isoformat()
    assert summary["n_fitted_maturities"] == 1
    assert summary["n_surface_params"] >= 1
    assert summary["code_version"] is not None
    assert summary["config_hashes"]

    raw_after = len(replay_day(ctx.store, trade_date, underlying="AAPL"))
    assert raw_after == raw_before


def test_sample_run_errors_when_the_store_has_no_committed_day(ctx: AppContext) -> None:
    pipeline = runner.PipelineRunner()
    job = pipeline.new_job("SAMPLE", ctx.default_underlying)
    pipeline.run_now(ctx, job)
    assert job.state == runner.JobState.ERROR
    assert "no committed sample day" in job.message
    assert job.finished_at is not None
