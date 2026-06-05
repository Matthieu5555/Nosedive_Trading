"""Deribit instrument discovery: translate /public/get_instruments into canonical OptionContracts.

The core logic is in ``parse_deribit_instrument_name`` — a pure function that maps a single
Deribit instrument name (e.g. ``BTC-25JUL25-100000-C``) to the canonical ``OptionContract``
used by the rest of the platform. ``discover_instruments`` wraps the REST call and applies
the maturity-window filter from config.

Multiplier is 1: one Deribit option contract covers 1 BTC or 1 ETH (cash-settled).
Currency is USD: prices are quoted in USD using the Deribit index price, not native BTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol

from algotrading.core.log import get_logger
from algotrading.infra.universe.contracts import OptionContract, Right

_log = get_logger(__name__)


class _RestTransport(Protocol):
    """The minimal REST surface discovery needs: a ``get`` returning the decoded payload.

    ``DeribitTransport`` and the test mock both satisfy it. The return is ``Any`` because the
    Deribit ``result`` is a list for ``get_instruments`` and a dict elsewhere.
    """

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...

# Deribit expiry label format in instrument names, e.g. "25JUL25".
_EXPIRY_FMT = "%d%b%y"

_MULTIPLIER = 1
_EXCHANGE = "DERIBIT"
_CURRENCY = "USD"
_SECURITY_TYPE = "OPT"


def parse_deribit_instrument_name(name: str) -> OptionContract:
    """Parse a Deribit instrument name into a canonical ``OptionContract``.

    Expected format: ``<SYMBOL>-<EXPIRY>-<STRIKE>-<RIGHT>``
    Example: ``BTC-25JUL25-100000-C``

    Raises ``ValueError`` on malformed input.
    """
    parts = name.split("-")
    if len(parts) != 4:
        raise ValueError(f"expected 4 dash-separated parts, got {len(parts)}: {name!r}")
    symbol, expiry_s, strike_s, right_s = parts
    if not symbol:
        raise ValueError(f"empty symbol in {name!r}")
    try:
        expiry = datetime.strptime(expiry_s, _EXPIRY_FMT).date()
    except ValueError as exc:
        raise ValueError(f"cannot parse expiry {expiry_s!r} in {name!r}: {exc}") from exc
    try:
        strike = Decimal(strike_s)
    except Exception as exc:
        raise ValueError(f"cannot parse strike {strike_s!r} in {name!r}: {exc}") from exc
    right = Right.from_raw(right_s)
    return OptionContract(
        symbol=symbol.upper(),
        expiry=expiry,
        strike=strike,
        right=right,
        multiplier=_MULTIPLIER,
        exchange=_EXCHANGE,
        currency=_CURRENCY,
        security_type=_SECURITY_TYPE,
    )


def discover_instruments(
    transport: _RestTransport,
    currency: str,
    *,
    as_of: date | None = None,
    min_days: int = 1,
    max_days: int = 180,
) -> list[OptionContract]:
    """Fetch all active option contracts for ``currency`` from Deribit and filter by maturity.

    ``transport`` must expose a ``.get(path, params) -> dict`` method
    (``DeribitTransport`` or any compatible mock).

    Only contracts whose expiry falls within [min_days, max_days] of ``as_of`` are returned.
    ``as_of`` is the reference date the maturity window is measured from — a **compute input**,
    so it is injected, not read from the wall clock: a replay or backtest passes the trade date
    and re-selects the exact same universe. When ``as_of`` is omitted it defaults to today (UTC),
    which is correct only for a live run; deterministic paths must pass it explicitly.

    Contracts that fail to parse are logged and skipped — discovery never aborts on a single
    bad instrument name.
    """
    raw = transport.get("/public/get_instruments", {"currency": currency, "kind": "option"})
    today = as_of if as_of is not None else datetime.now(tz=UTC).date()
    contracts: list[OptionContract] = []
    skipped = 0
    for item in raw:
        name: str = item.get("instrument_name", "")
        try:
            contract = parse_deribit_instrument_name(name)
        except ValueError as exc:
            _log.warning(
                "deribit_discovery_skip", extra={"instrument_name": name, "reason": str(exc)}
            )
            skipped += 1
            continue
        days_to_expiry = (contract.expiry - today).days
        if not (min_days <= days_to_expiry <= max_days):
            continue
        contracts.append(contract)
    _log.info(
        "deribit_discovery_done",
        extra={
            "currency": currency,
            "discovered": len(contracts),
            "skipped": skipped,
            "min_days": min_days,
            "max_days": max_days,
        },
    )
    return contracts
