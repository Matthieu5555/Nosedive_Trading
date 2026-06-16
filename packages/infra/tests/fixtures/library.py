from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from algotrading.core.config import ForwardConfig, SurfaceConfig
from algotrading.infra.contracts import InstrumentKey

from .quotes import ChainFixture, OptionQuoteFixture
from .synthetic import build_synthetic_surface

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)

SURFACE_CONFIG = SurfaceConfig(
    version="surface-test",
    svi_a_bounds=(0.0, 10.0),
    svi_b_bounds=(1e-8, 10.0),
    svi_rho_bounds=(-0.999, 0.999),
    svi_m_bounds=(-5.0, 5.0),
    svi_sigma_bounds=(1e-8, 10.0),
    svi_bound_hit_tol=1e-5,
    svi_max_iterations=200,
)

FORWARD_CONFIG = ForwardConfig(
    version="forward-test",
    good_rel_residual=1e-3,
    fair_rel_residual=1e-2,
    full_credit_pairs=4.0,
    rel_residual_halflife=1e-3,
    single_pair_confidence=0.30,
)

NEAR_EXPIRY = date(2026, 6, 19)
FAR_EXPIRY = date(2026, 9, 18)

_STALE_THRESHOLD_SECONDS = 30.0


def make_underlying(symbol: str) -> InstrumentKey:
    return InstrumentKey(
        underlying_symbol=symbol,
        security_type="STK",
        exchange="SMART",
        currency="USD",
        multiplier=1.0,
        broker_contract_id=f"u-{symbol}",
    )


def make_option(
    symbol: str,
    strike: float,
    right: str,
    expiry: date,
    *,
    multiplier: float = 100.0,
    currency: str = "USD",
) -> InstrumentKey:
    tag = f"{symbol}-{expiry.isoformat()}-{right}-{strike:g}"
    return InstrumentKey(
        underlying_symbol=symbol,
        security_type="OPT",
        exchange="SMART",
        currency=currency,
        multiplier=multiplier,
        broker_contract_id=f"o-{tag}",
        expiry=expiry,
        strike=strike,
        option_right=right,
    )


def _quote(
    instrument: InstrumentKey,
    bid: float | None,
    ask: float | None,
    last: float | None,
    quote_ts: datetime = AS_OF,
) -> OptionQuoteFixture:
    return OptionQuoteFixture(
        instrument=instrument, bid=bid, ask=ask, last=last, quote_ts=quote_ts
    )


def _liquid_chain(symbol: str, spot: float) -> ChainFixture:
    strikes = (spot - 10, spot - 5, spot, spot + 5, spot + 10)
    quotes = []
    for strike in strikes:
        call_mid = max(spot - strike, 0.0) + 3.0
        put_mid = max(strike - spot, 0.0) + 3.0
        call = make_option(symbol, strike, "C", NEAR_EXPIRY)
        put = make_option(symbol, strike, "P", NEAR_EXPIRY)
        quotes.append(_quote(call, call_mid - 0.10, call_mid + 0.10, call_mid))
        quotes.append(_quote(put, put_mid - 0.10, put_mid + 0.10, put_mid))
    return ChainFixture(
        name=f"liquid_{symbol.lower()}",
        description=f"Liquid {symbol} chain: five strikes, both rights, tight two-sided quotes.",
        as_of=AS_OF,
        underlying=make_underlying(symbol),
        underlying_spot=spot,
        quotes=tuple(quotes),
    )


def _crossed_quote_chain() -> ChainFixture:
    symbol = "AAPL"
    instrument = make_option(symbol, 100.0, "C", NEAR_EXPIRY)
    quotes = (_quote(instrument, 2.50, 2.00, 2.20),)
    return ChainFixture(
        name="crossed_quote",
        description="A crossed/locked quote where bid (2.50) exceeds ask (2.00).",
        as_of=AS_OF,
        underlying=make_underlying(symbol),
        underlying_spot=100.0,
        quotes=quotes,
    )


def _zero_bid_chain() -> ChainFixture:
    symbol = "AAPL"
    zero_bid = make_option(symbol, 130.0, "C", NEAR_EXPIRY)
    one_sided = make_option(symbol, 135.0, "C", NEAR_EXPIRY)
    quotes = (
        _quote(zero_bid, 0.0, 0.05, 0.02),
        _quote(one_sided, None, 0.05, None),
    )
    return ChainFixture(
        name="zero_bid",
        description="A zero-bid quote (bid=0) and a one-sided quote (no bid).",
        as_of=AS_OF,
        underlying=make_underlying(symbol),
        underlying_spot=100.0,
        quotes=quotes,
    )


def _single_strike_maturity() -> ChainFixture:
    symbol = "AAPL"
    instrument = make_option(symbol, 100.0, "C", FAR_EXPIRY)
    quotes = (_quote(instrument, 4.90, 5.10, 5.00),)
    return ChainFixture(
        name="single_strike_maturity",
        description="A maturity with exactly one strike — a degenerate surface slice.",
        as_of=AS_OF,
        underlying=make_underlying(symbol),
        underlying_spot=100.0,
        quotes=quotes,
    )


def _missing_multiplier_contract() -> ChainFixture:
    symbol = "AAPL"
    instrument = make_option(symbol, 100.0, "C", NEAR_EXPIRY, multiplier=0.0)
    quotes = (_quote(instrument, 2.90, 3.10, 3.00),)
    return ChainFixture(
        name="missing_multiplier",
        description="A contract whose multiplier is missing (encoded as 0.0).",
        as_of=AS_OF,
        underlying=make_underlying(symbol),
        underlying_spot=100.0,
        quotes=quotes,
    )


def _missing_currency_contract() -> ChainFixture:
    symbol = "AAPL"
    instrument = make_option(symbol, 100.0, "C", NEAR_EXPIRY, currency="")
    quotes = (_quote(instrument, 2.90, 3.10, 3.00),)
    return ChainFixture(
        name="missing_currency",
        description="A contract whose currency is missing (encoded as empty string).",
        as_of=AS_OF,
        underlying=make_underlying(symbol),
        underlying_spot=100.0,
        quotes=quotes,
    )


def _stale_option_chain() -> ChainFixture:
    symbol = "AAPL"
    instrument = make_option(symbol, 100.0, "C", NEAR_EXPIRY)
    stale_ts = AS_OF - timedelta(seconds=_STALE_THRESHOLD_SECONDS + 90.0)
    quotes = (_quote(instrument, 2.90, 3.10, 3.00, quote_ts=stale_ts),)
    return ChainFixture(
        name="stale_option",
        description="An option quote older than the staleness threshold.",
        as_of=AS_OF,
        underlying=make_underlying(symbol),
        underlying_spot=100.0,
        quotes=quotes,
    )


def _negative_or_zero_tte_chain() -> ChainFixture:
    symbol = "AAPL"
    expired = make_option(symbol, 100.0, "C", AS_OF.date() - timedelta(days=1))
    expiring_today = make_option(symbol, 100.0, "P", AS_OF.date())
    quotes = (
        _quote(expired, 2.90, 3.10, 3.00),
        _quote(expiring_today, 2.90, 3.10, 3.00),
    )
    return ChainFixture(
        name="negative_or_zero_tte",
        description="Options with negative time-to-expiry (expired) and zero (expiring today).",
        as_of=AS_OF,
        underlying=make_underlying(symbol),
        underlying_spot=100.0,
        quotes=quotes,
    )


def _synthetic_known_answer() -> ChainFixture:
    symbol = "AAPL"
    surface = build_synthetic_surface()
    quotes = []
    for point in surface.points:
        call = make_option(symbol, point.strike, "C", NEAR_EXPIRY)
        put = make_option(symbol, point.strike, "P", NEAR_EXPIRY)
        quotes.append(_quote(call, point.call_price, point.call_price, point.call_price))
        quotes.append(_quote(put, point.put_price, point.put_price, point.put_price))
    return ChainFixture(
        name="synthetic_known_answer",
        description=(
            "Prices generated from chosen sigma and SVI parameters; the forward, "
            "implied vols, and SVI fit are all analytically recoverable."
        ),
        as_of=AS_OF,
        underlying=make_underlying(symbol),
        underlying_spot=surface.forward * surface.discount_factor,
        quotes=tuple(quotes),
        known_answers=surface,
    )


def _build_all() -> dict[str, ChainFixture]:
    fixtures = [
        _liquid_chain("AAPL", 100.0),
        _liquid_chain("MSFT", 400.0),
        _liquid_chain("SPY", 500.0),
        _crossed_quote_chain(),
        _zero_bid_chain(),
        _single_strike_maturity(),
        _missing_multiplier_contract(),
        _missing_currency_contract(),
        _stale_option_chain(),
        _negative_or_zero_tte_chain(),
        _synthetic_known_answer(),
    ]
    return {fixture.name: fixture for fixture in fixtures}


ALL_FIXTURES: dict[str, ChainFixture] = _build_all()


def fixture_names() -> tuple[str, ...]:
    return tuple(sorted(ALL_FIXTURES))


def get_fixture(name: str) -> ChainFixture:
    return ALL_FIXTURES[name]
