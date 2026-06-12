"""Saxo Bank instrument discovery: 4-step workflow from underlying symbol to OptionContract list.

Step 1 — GET /ref/v1/instruments?Keywords=<symbol>&AssetTypes=EtfOption
          → OptionRootId, UnderlyingUic
Step 2 — GET /ref/v1/instruments/contractoptionspaces/<OptionRootId>
          → expiry matrix + UICs
Step 3 — parse each SpecificOption into an OptionContract (pure, no network)
Step 4 — (optional) GET /ref/v1/instruments/details for broker metadata

The public entry point is ``SaxoDiscovery.fetch()``. ``parse_saxo_option`` is exposed for
unit testing without a transport.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from algotrading.infra.universe.contracts import OptionContract, Right
from algotrading.infra_saxo.connectivity.saxo_transport import SaxoTransport

_ASSET_TYPE_MAP: dict[str, str] = {
    "EtfOption": "EtfOption",
    "StockOption": "StockOption",
}


@dataclass(frozen=True)
class SaxoUnderlying:
    """Resolved Saxo underlying metadata needed to drive option-chain discovery."""

    symbol: str
    uic: int
    option_root_id: int
    asset_type: str  # e.g. "EtfOption", "StockOption"
    currency: str
    exchange: str = "SAXO"


class DiscoveryError(Exception):
    """Raised when instrument discovery cannot complete (missing data, API error)."""


def parse_saxo_option(
    specific_option: dict,
    *,
    symbol: str,
    expiry: date,
    currency: str,
    exchange: str = "SAXO",
    multiplier: int = 100,
) -> OptionContract:
    """Map one ``SpecificOptions`` dict from contractoptionspaces to an ``OptionContract``.

    Pure function — no network I/O. Raises ``DiscoveryError`` on malformed input.
    """
    try:
        put_call = specific_option["PutCall"]
        strike = Decimal(str(specific_option["StrikePrice"]))
        uic = int(specific_option["Uic"])
    except Exception as exc:  # noqa: BLE001 — Decimal.InvalidOperation + KeyError + TypeError
        raise DiscoveryError(f"Malformed SpecificOptions entry: {specific_option!r}") from exc

    right = Right.CALL if put_call.lower() == "call" else Right.PUT

    return OptionContract(
        symbol=symbol,
        expiry=expiry,
        strike=strike,
        right=right,
        multiplier=multiplier,
        exchange=exchange,
        currency=currency,
        broker_contract_id=str(uic),
        raw={"saxo_uic": uic, "put_call": put_call},
    )


class SaxoDiscovery:
    """Resolve an underlying symbol into a full list of ``OptionContract`` objects.

    Uses the Saxo contractoptionspaces endpoint which returns the complete option matrix
    (all expiries, all strikes) in one call — no pagination needed for discovery.
    """

    def __init__(self, transport: SaxoTransport) -> None:
        self._t = transport

    def resolve_underlying(self, symbol: str, asset_type: str = "EtfOption") -> SaxoUnderlying:
        """Step 1: resolve ``symbol`` to its Saxo UIC and OptionRootId.

        Symbol can be "SPY" or "SPY:xcbf" (with exchange). Both are accepted.
        """
        # Extract clean symbol (before the `:` if present)
        symbol_key = symbol.split(":")[0]
        resp = self._t.get(
            "/ref/v1/instruments",
            params={"Keywords": symbol_key, "AssetTypes": asset_type, "$top": 10},
        )
        instruments = resp.get("Data", [])
        for inst in instruments:
            inst_symbol = inst.get("Symbol", "")
            # Match on the base symbol (before `:`) in case Saxo qualifies with exchange
            inst_base = inst_symbol.split(":")[0].upper()
            if inst_base == symbol_key.upper():
                option_root_id = inst.get("OptionRootId") or inst.get("Identifier")
                if option_root_id is None:
                    raise DiscoveryError(f"No OptionRootId for {symbol} in {inst!r}")
                return SaxoUnderlying(
                    symbol=symbol_key,  # Use clean symbol, not Saxo-qualified
                    uic=int(inst["Identifier"]),
                    option_root_id=int(option_root_id),
                    asset_type=asset_type,
                    currency=inst.get("CurrencyCode", "USD"),
                    exchange=inst.get("ExchangeId", "SAXO"),
                )
        raise DiscoveryError(f"Symbol {symbol!r} not found in Saxo instruments ({asset_type})")

    def fetch_option_space(self, underlying: SaxoUnderlying) -> list[OptionContract]:
        """Steps 2+3: fetch the contractoptionspaces matrix and parse all contracts."""
        resp = self._t.get(
            f"/ref/v1/instruments/contractoptionspaces/{underlying.option_root_id}",
        )
        option_spaces = resp.get("OptionSpace", [])
        contracts: list[OptionContract] = []
        for space in option_spaces:
            raw_expiry = space.get("DisplayExpiry") or space.get("Expiry", "")
            try:
                expiry = date.fromisoformat(raw_expiry[:10])
            except ValueError as exc:
                raise DiscoveryError(f"Cannot parse expiry {raw_expiry!r}") from exc

            for specific in space.get("SpecificOptions", []):
                parsed = parse_saxo_option(
                    specific,
                    symbol=underlying.symbol,
                    expiry=expiry,
                    currency=underlying.currency,
                    exchange=f"SAXO_{underlying.uic}",
                )
                # Build a NEW contract via dataclasses.replace — never mutate the frozen
                # dataclass. broker_contract_id becomes the underlying UIC (the adapter needs
                # it for the chain subscription); the real exchange and the strike's own UIC
                # move into raw for completeness. Both fields are compare=False metadata, so
                # identity (and the canonical instrument key) is unchanged.
                contracts.append(
                    replace(
                        parsed,
                        broker_contract_id=str(underlying.uic),
                        raw={
                            **dict(parsed.raw or {}),
                            "exchange": underlying.exchange,
                            "strike_uic": int(specific.get("Uic", 0)),
                        },
                    )
                )
        return contracts

    def fetch(self, symbol: str, asset_type: str = "EtfOption") -> list[OptionContract]:
        """Full discovery: symbol → SaxoUnderlying → list[OptionContract]."""
        underlying = self.resolve_underlying(symbol, asset_type)
        return self.fetch_option_space(underlying)
