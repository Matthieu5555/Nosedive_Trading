"""Saxo underlying spot probe: poll InfoPrices for the stock and emit Underlying-keyed ticks.

The OptionsChain stream carries only option strikes, but the snapshot builder additionally needs a
reference spot for the underlying (without it, it raises). Saxo exposes the stock quote via the
``/trade/v1/infoprices`` snapshot endpoint (delayed-free on a funded account). A low-frequency poll
is sufficient: the underlying spot does not move on a sub-second scale, and the snapshot builder
takes the latest observation at or before its as_of. Read-only (GET), no orders.
"""

from __future__ import annotations

import math
from typing import Any, Protocol

from algotrading.core.log import get_logger
from algotrading.infra.collectors.normalize import BrokerTick
from algotrading.infra.universe import Underlying, instrument_key

from .saxo_adapter import _parse_last_updated

_log = get_logger(__name__)


class _SupportsGet(Protocol):
    """The minimal Saxo REST surface the probe needs (``SaxoTransport`` satisfies it)."""

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...

# Saxo InfoPrices Quote field -> canonical EAV field. Mid is mapped to ``last`` as the snapshot
# builder's last-resort reference spot when bid/ask are unusable (crossed/wide/absent).
_QUOTE_FIELD_MAP = (("Bid", "bid"), ("Ask", "ask"), ("Mid", "last"))


class SaxoUnderlyingProbe:
    """Poll the Saxo stock InfoPrices snapshot and emit ``Underlying``-keyed quote ticks.

    The Uic is resolved once (lazily) from ``/ref/v1/instruments`` and cached. Each ``fetch``
    returns one tick per available quote field; an unentitled or empty quote yields an empty list
    (logged, never raised) so a capture loop keeps running across closed markets and gaps.
    """

    def __init__(
        self, transport: _SupportsGet, *, symbol: str, currency: str, asset_type: str = "Stock"
    ) -> None:
        self._transport = transport
        self._symbol = symbol.upper()
        self._currency = currency
        self._asset_type = asset_type
        self._uic: int | None = None
        self._resolved = False  # True once resolution was attempted (success OR permanent failure)
        self._key: str | None = None

    def _resolve_uic(self) -> int | None:
        ref = self._transport.get(
            "/ref/v1/instruments",
            {"Keywords": self._symbol, "AssetTypes": self._asset_type},
        )
        rows = ref.get("Data", []) if isinstance(ref, dict) else []
        return rows[0].get("Identifier") if rows else None

    def fetch(self) -> list[BrokerTick]:
        """Return current underlying quote ticks (bid/ask/last), or [] if unavailable."""
        if not self._resolved:
            # Reference-data lookup is market-hours-independent: a miss means the symbol is
            # genuinely unknown, so cache the failure (resolve once), not re-hit it every poll.
            self._resolved = True
            self._uic = self._resolve_uic()
            if self._uic is None:
                _log.warning("saxo underlying Uic unresolved", extra={"symbol": self._symbol})
            else:
                # SAXO_<uic> mirrors the option key's exchange segment so the key round-trips.
                self._key = instrument_key(
                    Underlying(
                        symbol=self._symbol,
                        exchange=f"SAXO_{self._uic}",
                        currency=self._currency,
                        security_type="STK",
                    )
                )
        if self._uic is None:
            return []

        info = self._transport.get(
            "/trade/v1/infoprices",
            {"Uic": self._uic, "AssetType": self._asset_type, "FieldGroups": "Quote"},
        )
        quote = info.get("Quote", {}) if isinstance(info, dict) else {}
        err = quote.get("ErrorCode")
        if quote.get("PriceTypeBid") == "NoAccess" or (err and str(err) != "None"):
            _log.warning(
                "saxo underlying quote not entitled",
                extra={"symbol": self._symbol, "error": str(err)},
            )
            return []

        # _key is set whenever _uic resolved; the early return on _uic is None guarantees it.
        assert self._key is not None
        ts = _parse_last_updated(info) or _parse_last_updated(quote)
        ticks: list[BrokerTick] = []
        for saxo_field, field_name in _QUOTE_FIELD_MAP:
            value = quote.get(saxo_field)
            if value is None:
                continue
            num = float(value)
            if not math.isfinite(num):
                continue
            ticks.append(
                BrokerTick(
                    instrument_key=self._key,
                    field_name=field_name,
                    value=num,
                    underlying=self._symbol,
                    provider="SAXO",
                    exchange_ts=ts,
                )
            )
        return ticks
