"""End-to-end test for `orchestration.build_surface` — the reusable surface use case.

Drives the job over a scripted in-memory broker session built from the
``synthetic_known_answer`` chain fixture (the same fixture the handover e2e and golden
replay tests use), so a real SVI surface is fitted through the exact actor pipeline. The
test asserts the job composes the steps correctly: a surface is produced and persisted, the
slice summaries are exactly the reduction of the persisted parameters, and the market-data
status reflects the feed — both for a clean fake session and for one reporting an
entitlement downgrade.
"""

from __future__ import annotations

from pathlib import Path

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
from orchestration import (
    MarketDataDiagnostics,
    SurfaceJobRequest,
    SurfaceJobResult,
    build_surface,
)
from storage import ParquetStore
from surfaces import summarize_surface_parameters
from universe import ChainSelection

_CONFIG_PATH = Path(__file__).resolve().parents[1].parent / "configs" / "default.toml"
_CHAIN = get_fixture("synthetic_known_answer")
_AS_OF = _CHAIN.as_of
_CALC_TS = _AS_OF
_TRADE_DATE = _AS_OF.date()


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


def _quote_ticks(instrument: InstrumentKey, *, bid: float, ask: float, last: float,
                 start_sequence: int) -> list[BrokerTick]:
    cid = instrument.broker_contract_id
    return [
        BrokerTick(cid, "bid", bid, sequence=start_sequence, exchange_ts=_AS_OF),
        BrokerTick(cid, "ask", ask, sequence=start_sequence + 1, exchange_ts=_AS_OF),
        BrokerTick(cid, "last", last, sequence=start_sequence + 2, exchange_ts=_AS_OF),
    ]


def _chain_rows_and_script(
    chain: ChainFixture,
) -> tuple[tuple[dict[str, object], ...], list[BrokerTick]]:
    """Turn the fixture into (broker chain rows, a tick script) for a FakeBrokerSession."""
    spot = chain.underlying_spot
    rows = [_broker_row(chain.underlying)]
    script = _quote_ticks(chain.underlying, bid=spot - 0.05, ask=spot + 0.05, last=spot,
                          start_sequence=0)
    seq = 3
    for quote in chain.quotes:
        rows.append(_broker_row(quote.instrument))
        # The fixture uses None for a one-sided quote; the known-answer chain is two-sided.
        assert quote.bid is not None and quote.ask is not None and quote.last is not None
        script += _quote_ticks(quote.instrument, bid=quote.bid, ask=quote.ask, last=quote.last,
                               start_sequence=seq)
        seq += 3
    return tuple(rows), script


class _FakeDiagnostics:
    """A session that reports a live request downgraded to delayed with an entitlement notice."""

    requested_market_data_type = 1
    observed_market_data_type = 3

    def feed_errors(self) -> tuple[tuple[int, str], ...]:
        return ((10091, "Requested market data is not subscribed; displaying delayed"),)


def _run(
    tmp_path: Path, *, diagnostics: MarketDataDiagnostics | None = None
) -> tuple[ParquetStore, SurfaceJobResult]:
    rows, script = _chain_rows_and_script(_CHAIN)
    store = ParquetStore(tmp_path)
    clock = ManualClock(start=_AS_OF)
    config = load_config(_CONFIG_PATH)
    session = FakeBrokerSession(chains={_CHAIN.underlying.underlying_symbol: rows}, script=script)
    supervisor = SessionSupervisor(session, client_id=client_id_for("smoke"), clock=clock)
    supervisor.connect()
    request = SurfaceJobRequest(
        symbol=_CHAIN.underlying.underlying_symbol,
        trade_date=_TRADE_DATE,
        selection=ChainSelection(),
        market_data_type=3,
        as_of=_AS_OF,
        calc_ts=_CALC_TS,
    )
    return store, build_surface(
        request=request, store=store, config=config, config_hash=config_hash(config),
        supervisor=supervisor, clock=clock, correlation_id="surface-test",
        diagnostics=diagnostics,
    )


def test_build_surface_produces_persists_and_summarizes_a_surface(tmp_path: Path) -> None:
    store, result = _run(tmp_path)

    # A real surface was fitted and persisted through the actor pipeline.
    assert len(result.outputs.surface_parameters) > 0
    assert len(store.read("surface_parameters")) == len(result.outputs.surface_parameters)
    # The summaries are exactly the reduction of the persisted parameters — no side channel.
    assert result.slices == summarize_surface_parameters(result.outputs.surface_parameters)
    assert result.fitted_maturities == len(result.slices) > 0
    # Every fitted slice has a finite, positive ATM vol.
    assert all(s.atm_vol > 0.0 for s in result.slices)


def test_build_surface_reports_a_clean_feed_for_a_plain_session(tmp_path: Path) -> None:
    _store, result = _run(tmp_path)
    status = result.market_data_status

    # A fake session reports no entitlement diagnostics, but the counts are real.
    assert status.subscribed == 1 + len(_CHAIN.quotes)  # underlying + every option
    assert status.producing > 0
    assert status.is_usable is True
    assert status.has_entitlement_problem is False


def test_build_surface_surfaces_an_entitlement_downgrade_from_diagnostics(tmp_path: Path) -> None:
    _store, result = _run(tmp_path, diagnostics=_FakeDiagnostics())
    status = result.market_data_status

    assert status.requested_type == 1 and status.effective_type == 3
    assert status.downgraded is True
    assert status.has_entitlement_problem is True
    assert "10091" in status.describe()
