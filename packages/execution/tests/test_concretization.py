from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core import source_ref, stamp
from algotrading.execution import concretization as concretization_module
from algotrading.execution.concretization import (
    MARK_SOURCE_ANALYTICS_PRICE,
    MARK_SOURCE_SNAPSHOT_MID,
    ConcreteChain,
    ConcreteFill,
    ConcretizationError,
    concretize,
    option_right_for_band,
)
from algotrading.infra.contracts import (
    InstrumentKey,
    MarketStateSnapshot,
    ProjectedOptionAnalytics,
)
from algotrading.infra.orders import Market, Side, TicketLeg
from algotrading.infra.pricing import UNIT_STRINGS

_TS = datetime(2026, 6, 12, 21, 0, tzinfo=UTC)
_AS_OF = date(2026, 6, 12)
_UND = "SX5E"
_EXCHANGE = "EUREX"
_CURRENCY = "EUR"
_MULTIPLIER = 10.0


def _prov():
    return stamp(
        calc_ts=_TS,
        code_version="algotrading-infra-0.1.0",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("raw_market_events", "s", "e"),),
        source_timestamps=(_TS,),
    )


def _analytics_row(
    *,
    delta_band: str,
    target_delta: float,
    strike: float,
    price: float,
    tenor_label: str = "3m",
    underlying: str = _UND,
    provider: str = "ibkr",
) -> ProjectedOptionAnalytics:
    return ProjectedOptionAnalytics(
        snapshot_ts=_TS,
        provider=provider,
        underlying=underlying,
        tenor_label=tenor_label,
        maturity_years=0.25,
        delta_band=delta_band,
        target_delta=target_delta,
        log_moneyness=0.0,
        strike=strike,
        forward_price=5000.0,
        implied_vol=0.2,
        total_variance=0.2 * 0.2 * 0.25,
        price=price,
        delta=0.5,
        gamma=0.02,
        vega=0.31,
        theta=-0.05,
        rho=0.04,
        dollar_delta=1.0,
        dollar_gamma=2.0,
        dollar_vega=3.0,
        dollar_delta_unit=UNIT_STRINGS["dollar_delta"],
        dollar_gamma_unit=UNIT_STRINGS["dollar_gamma_one_pct"],
        dollar_vega_unit=UNIT_STRINGS["dollar_vega"],
        model_version="svi-test",
        pricer_version="px-test",
        source_snapshot_ts=_TS,
        provenance=_prov(),
    )


def _listed(
    *,
    strike: float,
    right: str,
    expiry: date,
    broker_contract_id: str,
    underlying: str = _UND,
) -> InstrumentKey:
    return InstrumentKey(
        underlying_symbol=underlying,
        security_type="OPT",
        exchange=_EXCHANGE,
        currency=_CURRENCY,
        multiplier=_MULTIPLIER,
        broker_contract_id=broker_contract_id,
        expiry=expiry,
        strike=strike,
        option_right=right,
    )


def _snapshot(*, contract_key: str, bid: float, ask: float) -> MarketStateSnapshot:
    return MarketStateSnapshot(
        snapshot_ts=_TS,
        instrument_key=contract_key,
        reference_spot=5000.0,
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2.0,
        spread_pct=(ask - bid) / ((bid + ask) / 2.0),
        reference_type="mid",
        flags=(),
        completeness=1.0,
        trade_date=_AS_OF,
        underlying=_UND,
        provenance=_prov(),
    )


_CALL_STRIKE = 5200.0
_FRONT_EXPIRY = date(2026, 9, 18)
_FAR_EXPIRY = date(2027, 9, 17)
_FRONT_CONID = "OPT-SX5E-5200C-2026-09"
_FAR_CONID = "OPT-SX5E-5200C-2027-09"


def _front_call_instrument() -> InstrumentKey:
    return _listed(
        strike=_CALL_STRIKE, right="C", expiry=_FRONT_EXPIRY, broker_contract_id=_FRONT_CONID
    )


def _ticket_leg(
    *, side: Side = Side.BUY, quantity: float = 2.0, delta_band: str = "30dc"
) -> TicketLeg:
    return TicketLeg(
        instrument_kind="option",
        underlying=_UND,
        side=side,
        quantity=quantity,
        price_spec=Market(),
        tenor_label="3m",
        delta_band=delta_band,
    )


def _chain(*, with_snapshot: bool = True) -> ConcreteChain:
    front = _front_call_instrument()
    snapshots = (
        [_snapshot(contract_key=front.canonical(), bid=40.0, ask=44.0)] if with_snapshot else []
    )
    return ConcreteChain.build(
        analytics_rows=[
            _analytics_row(
                delta_band="30dc", target_delta=0.30, strike=_CALL_STRIKE, price=41.5
            )
        ],
        listed_contracts=[
            front,
            _listed(
                strike=_CALL_STRIKE, right="C", expiry=_FAR_EXPIRY, broker_contract_id=_FAR_CONID
            ),
        ],
        snapshots=snapshots,
    )


def test_concretize_emits_the_seam_fill_field_by_field() -> None:
    expected_instrument = _front_call_instrument()
    expected_key = expected_instrument.canonical()

    fill = concretize(_ticket_leg(), as_of=_AS_OF, chain=_chain())

    assert isinstance(fill, ConcreteFill)
    assert fill.instrument == expected_instrument
    assert fill.contract_key == expected_key
    assert fill.instrument.strike == _CALL_STRIKE
    assert fill.instrument.expiry == _FRONT_EXPIRY
    assert fill.instrument.option_right == "C"
    assert fill.underlying == _UND
    assert fill.side is Side.BUY
    assert fill.quantity == 2.0
    assert fill.tenor_label == "3m"
    assert fill.delta_band == "30dc"
    assert fill.as_of == _AS_OF
    assert fill.fill_price == pytest.approx(42.0)
    assert fill.mark_source == MARK_SOURCE_SNAPSHOT_MID


def test_concretize_is_deterministic() -> None:
    a = concretize(_ticket_leg(), as_of=_AS_OF, chain=_chain())
    b = concretize(_ticket_leg(), as_of=_AS_OF, chain=_chain())
    assert a == b


def test_put_band_resolves_to_a_put_contract() -> None:
    put_strike = 4800.0
    put_expiry = date(2026, 9, 18)
    put = _listed(strike=put_strike, right="P", expiry=put_expiry, broker_contract_id="P-CONID")
    chain = ConcreteChain.build(
        analytics_rows=[
            _analytics_row(
                delta_band="30dp", target_delta=-0.30, strike=put_strike, price=37.0
            )
        ],
        listed_contracts=[put],
        snapshots=[_snapshot(contract_key=put.canonical(), bid=36.0, ask=38.0)],
    )
    fill = concretize(_ticket_leg(side=Side.SELL, delta_band="30dp"), as_of=_AS_OF, chain=chain)
    assert fill.instrument.option_right == "P"
    assert fill.instrument.strike == put_strike
    assert fill.fill_price == pytest.approx(37.0)


def test_old_date_replay_resolves_the_old_chain_never_today() -> None:
    old_as_of = date(2025, 6, 12)
    old_expiry = date(2025, 9, 19)
    old_contract = _listed(
        strike=_CALL_STRIKE, right="C", expiry=old_expiry, broker_contract_id="OLD-CONID"
    )
    old_chain = ConcreteChain.build(
        analytics_rows=[
            _analytics_row(
                delta_band="30dc", target_delta=0.30, strike=_CALL_STRIKE, price=30.0
            )
        ],
        listed_contracts=[old_contract],
        snapshots=[_snapshot(contract_key=old_contract.canonical(), bid=29.0, ask=31.0)],
    )
    fill = concretize(_ticket_leg(), as_of=old_as_of, chain=old_chain)
    assert fill.instrument.expiry == old_expiry
    assert fill.instrument.expiry != _FRONT_EXPIRY
    assert fill.as_of == old_as_of


def test_already_expired_listing_is_not_resolvable() -> None:
    expired = _listed(
        strike=_CALL_STRIKE, right="C", expiry=date(2026, 3, 20), broker_contract_id="EXP"
    )
    chain = ConcreteChain.build(
        analytics_rows=[
            _analytics_row(
                delta_band="30dc", target_delta=0.30, strike=_CALL_STRIKE, price=41.5
            )
        ],
        listed_contracts=[expired],
        snapshots=[],
    )
    with pytest.raises(ConcretizationError) as exc:
        concretize(_ticket_leg(), as_of=_AS_OF, chain=chain)
    assert exc.value.reason == "no_listed_contract"


def test_mark_falls_back_to_analytics_price_when_no_quote() -> None:
    fill = concretize(_ticket_leg(), as_of=_AS_OF, chain=_chain(with_snapshot=False))
    assert fill.fill_price == pytest.approx(41.5)
    assert fill.mark_source == MARK_SOURCE_ANALYTICS_PRICE


def test_no_finite_mark_is_a_labelled_failure() -> None:
    front = _front_call_instrument()
    chain = ConcreteChain.build(
        analytics_rows=[
            _analytics_row(delta_band="30dc", target_delta=0.30, strike=_CALL_STRIKE, price=0.0)
        ],
        listed_contracts=[front],
        snapshots=[],
    )
    with pytest.raises(ConcretizationError) as exc:
        concretize(_ticket_leg(), as_of=_AS_OF, chain=chain)
    assert exc.value.reason == "no_mark"


def test_missing_analytics_row_is_labelled() -> None:
    chain = ConcreteChain.build(
        analytics_rows=[],
        listed_contracts=[_front_call_instrument()],
        snapshots=[],
    )
    with pytest.raises(ConcretizationError) as exc:
        concretize(_ticket_leg(), as_of=_AS_OF, chain=chain)
    assert exc.value.reason == "no_analytics_row"
    assert exc.value.cell == (_UND, "3m", "30dc")


def test_provider_ambiguous_cell_is_labelled() -> None:
    chain = ConcreteChain.build(
        analytics_rows=[
            _analytics_row(
                delta_band="30dc", target_delta=0.30, strike=_CALL_STRIKE, price=41.5,
                provider="ibkr",
            ),
            _analytics_row(
                delta_band="30dc", target_delta=0.30, strike=_CALL_STRIKE, price=41.5,
                provider="saxo",
            ),
        ],
        listed_contracts=[_front_call_instrument()],
        snapshots=[],
    )
    with pytest.raises(ConcretizationError) as exc:
        concretize(_ticket_leg(), as_of=_AS_OF, chain=chain)
    assert exc.value.reason == "provider_ambiguous"


def test_no_listed_contract_for_strike_is_labelled() -> None:
    chain = ConcreteChain.build(
        analytics_rows=[
            _analytics_row(
                delta_band="30dc", target_delta=0.30, strike=5201.0, price=41.5
            )
        ],
        listed_contracts=[_front_call_instrument()],
        snapshots=[],
    )
    with pytest.raises(ConcretizationError) as exc:
        concretize(_ticket_leg(), as_of=_AS_OF, chain=chain)
    assert exc.value.reason == "no_listed_contract"


def test_strike_ambiguous_when_two_listings_tie_on_the_front_expiry() -> None:
    tie_a = _listed(
        strike=_CALL_STRIKE, right="C", expiry=_FRONT_EXPIRY, broker_contract_id="TIE-A"
    )
    tie_b = _listed(
        strike=_CALL_STRIKE, right="C", expiry=_FRONT_EXPIRY, broker_contract_id="TIE-B"
    )
    chain = ConcreteChain.build(
        analytics_rows=[
            _analytics_row(
                delta_band="30dc", target_delta=0.30, strike=_CALL_STRIKE, price=41.5
            )
        ],
        listed_contracts=[tie_a, tie_b],
        snapshots=[],
    )
    with pytest.raises(ConcretizationError) as exc:
        concretize(_ticket_leg(), as_of=_AS_OF, chain=chain)
    assert exc.value.reason == "strike_ambiguous"


def test_stock_leg_is_rejected() -> None:
    stock = TicketLeg(
        instrument_kind="stock",
        underlying=_UND,
        side=Side.SELL,
        quantity=5.0,
        price_spec=Market(),
    )
    with pytest.raises(ConcretizationError) as exc:
        concretize(stock, as_of=_AS_OF, chain=_chain())
    assert exc.value.reason == "not_an_option_leg"


@pytest.mark.parametrize(
    ("delta_band", "target_delta"),
    [("30dc", 0.30), ("30dp", -0.30), ("02dc", 0.02), ("02dp", -0.02),
     ("atm", 0.0), ("atmp", 0.0)],
)
def test_option_right_for_band_matches_projection_rule(
    delta_band: str, target_delta: float
) -> None:
    from algotrading.infra.surfaces.projection import _option_right_for_band

    assert option_right_for_band(delta_band, target_delta) == _option_right_for_band(
        delta_band, target_delta
    )


_FORBIDDEN_NAMES = frozenset({
    "environ", "getenv", "load_dotenv", "api_key", "credential", "password", "secret",
    "transmit", "place_order", "submit_order", "send_order", "BrokerTransport",
})
_FORBIDDEN_IMPORT_SUBSTRINGS = ("infra_ibkr", "connectivity", "dotenv")


def _concretization_code_names() -> tuple[set[str], set[str]]:
    import ast

    path = Path(concretization_module.__file__)
    names: set[str] = set()
    imports: set[str] = set()
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names, imports


def test_concretization_never_transmits_and_reads_no_credentials() -> None:
    names, imports = _concretization_code_names()
    assert not (names & _FORBIDDEN_NAMES), f"forbidden symbol(s): {names & _FORBIDDEN_NAMES}"
    assert "os" not in imports, "concretization must not import os (no env reads)"
    leaked = [m for m in imports for s in _FORBIDDEN_IMPORT_SUBSTRINGS if s in m]
    assert leaked == [], f"concretization must not import: {leaked}"
