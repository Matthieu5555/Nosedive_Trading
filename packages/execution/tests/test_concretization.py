"""Fill concretization (ADR 0043): grid-cell ticket leg -> concrete, priced paper fill.

The independent oracle in every test is hand-built: the expected ``(strike, expiry, right)``,
the expected canonical ``contract_key`` and the expected paper mark are written out here from the
fixture chain, never read back from the code under test. The named test surfaces from
``tasks/execution-fill-concretization.md`` are covered:

* deterministic + as-of resolution (same cell+chain → same contract; an old-date replay resolves
  the old chain's contract, never today's — the look-ahead guard);
* the paper mark is the as-of snapshot mid by the stated rule, with a hand-computed oracle, and
  falls back to the analytics model price when no quote exists;
* the seam round-trip — the emitted :class:`ConcreteFill` carries exactly the fields
  booking-commit consumes, asserted field-by-field so a rename breaks loudly;
* no broker / no credential — an AST-level scan mirroring ``test_order_ticket.py``;
* an unresolvable cell is a labelled :class:`ConcretizationError`, never a silent default.
"""

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
    """One WS-1F grid cell: the resolved strike + the model price are the resolver's inputs."""
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
    """A concrete listed option contract on the captured chain."""
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
    """A two-sided quote for the resolved contract, the mid source for the paper mark."""
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


# A 3m 30Δ-call grid cell, solved to strike 5200; the chain lists that contract at the front
# expiry 2026-09-18 and a far expiry 2027-09-17. The mid of (40, 44) is 42.0 (hand-computed).
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


# --- Seam round-trip + resolution -------------------------------------------------------------


def test_concretize_emits_the_seam_fill_field_by_field() -> None:
    # Independent oracle: the expected concrete contract and key are hand-built here.
    expected_instrument = _front_call_instrument()
    expected_key = expected_instrument.canonical()

    fill = concretize(_ticket_leg(), as_of=_AS_OF, chain=_chain())

    assert isinstance(fill, ConcreteFill)
    # The concrete identity off the captured chain (front expiry, not the far one).
    assert fill.instrument == expected_instrument
    assert fill.contract_key == expected_key
    assert fill.instrument.strike == _CALL_STRIKE
    assert fill.instrument.expiry == _FRONT_EXPIRY
    assert fill.instrument.option_right == "C"
    # Carried straight from the ticket leg.
    assert fill.underlying == _UND
    assert fill.side is Side.BUY
    assert fill.quantity == 2.0
    # Grid provenance kept for traceability back to the planning intention.
    assert fill.tenor_label == "3m"
    assert fill.delta_band == "30dc"
    assert fill.as_of == _AS_OF
    # Paper mark = snapshot mid (40 + 44) / 2 = 42.0 (hand-computed), labelled as such.
    assert fill.fill_price == pytest.approx(42.0)
    assert fill.mark_source == MARK_SOURCE_SNAPSHOT_MID


def test_concretize_is_deterministic() -> None:
    # Same (cell, as_of, chain) resolves to the same contract + price every call.
    a = concretize(_ticket_leg(), as_of=_AS_OF, chain=_chain())
    b = concretize(_ticket_leg(), as_of=_AS_OF, chain=_chain())
    assert a == b


def test_put_band_resolves_to_a_put_contract() -> None:
    # The band suffix governs the right: a 30dp cell -> a P contract at its solved strike.
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
    assert fill.fill_price == pytest.approx(37.0)  # mid of (36, 38)


# --- As-of / look-ahead guard -----------------------------------------------------------------


def test_old_date_replay_resolves_the_old_chain_never_today() -> None:
    # The as-of chain is the only source of contracts. An old-date booking is handed the old
    # chain (an old listed expiry); it can never resolve to a contract the old chain didn't hold.
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
    # Crucially NOT today's front expiry from the current chain.
    assert fill.instrument.expiry != _FRONT_EXPIRY
    assert fill.as_of == old_as_of


def test_already_expired_listing_is_not_resolvable() -> None:
    # A chain whose only listing for the strike already expired as-of the booking date is a
    # labelled failure, never a backward-dated contract.
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


# --- Paper mark rule --------------------------------------------------------------------------


def test_mark_falls_back_to_analytics_price_when_no_quote() -> None:
    # No snapshot for the resolved contract -> the stated fallback is the analytics model price.
    fill = concretize(_ticket_leg(), as_of=_AS_OF, chain=_chain(with_snapshot=False))
    assert fill.fill_price == pytest.approx(41.5)  # the analytics row's model price
    assert fill.mark_source == MARK_SOURCE_ANALYTICS_PRICE


def test_no_finite_mark_is_a_labelled_failure() -> None:
    # No snapshot and a non-positive analytics price -> labelled "no_mark", never a silent zero.
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


# --- Labelled failures for unresolvable cells -------------------------------------------------


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
    # Two providers seed the same cell -> never silently pick one.
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
    # The analytics cell solved a strike the captured chain does not list.
    chain = ConcreteChain.build(
        analytics_rows=[
            _analytics_row(
                delta_band="30dc", target_delta=0.30, strike=5201.0, price=41.5
            )
        ],
        listed_contracts=[_front_call_instrument()],  # only strike 5200 is listed
        snapshots=[],
    )
    with pytest.raises(ConcretizationError) as exc:
        concretize(_ticket_leg(), as_of=_AS_OF, chain=chain)
    assert exc.value.reason == "no_listed_contract"


def test_strike_ambiguous_when_two_listings_tie_on_the_front_expiry() -> None:
    # Two distinct listed contracts at the same (strike, right, expiry) -> refuse to guess.
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
    # A stock leg has no grid cell to concretize; it is a labelled rejection, not a crash.
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


# --- option_right_for_band agrees with the projection authority -------------------------------


@pytest.mark.parametrize(
    ("delta_band", "target_delta"),
    [("30dc", 0.30), ("30dp", -0.30), ("02dc", 0.02), ("02dp", -0.02),
     ("atm", 0.0), ("atmp", 0.0)],
)
def test_option_right_for_band_matches_projection_rule(
    delta_band: str, target_delta: float
) -> None:
    # The public twin must never drift from the projection's private authority — assert equality
    # against it directly (a test may reach into internals to pin a contract).
    from algotrading.infra.surfaces.projection import _option_right_for_band

    assert option_right_for_band(delta_band, target_delta) == _option_right_for_band(
        delta_band, target_delta
    )


# --- The no-broker / no-credential safety gate, made a falsifiable AST test --------------------
# Mirrors test_order_ticket.py: scan the module's AST so docstrings never trip it, only real code.

_FORBIDDEN_NAMES = frozenset({
    "environ", "getenv", "load_dotenv", "api_key", "credential", "password", "secret",
    "transmit", "place_order", "submit_order", "send_order", "BrokerTransport",
})
_FORBIDDEN_IMPORT_SUBSTRINGS = ("infra_ibkr", "connectivity", "dotenv")


def _concretization_code_names() -> tuple[set[str], set[str]]:
    """Every identifier and imported-module path used in the concretization module's code."""
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
