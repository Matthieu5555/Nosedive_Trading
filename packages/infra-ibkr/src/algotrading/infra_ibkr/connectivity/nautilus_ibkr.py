"""Build the Nautilus InteractiveBrokers data-client config — the IBKR live seam.

ADR 0023/0025: IBKR connectivity is Nautilus's shipped InteractiveBrokers adapter
(retiring the hand-rolled ``ib_async`` session). The adapter and its config live behind
the ``ibkr`` extra (``nautilus-trader[ib]`` → ``ibapi``), which is **absent from the gate
env** by design — a live connect needs a running TWS/IB Gateway, which CI does not have.
So this builder imports the adapter lazily and raises a clear, actionable error when the
extra is missing; the config it returns is what a live ``TradingNode`` is handed on a
machine with a Gateway.

The verifiable boundary stops at config construction: the wiring above it (instrument
selection, the live ``TradingNode`` run) is exercised on a Gateway host, not in CI.
"""

from collections.abc import Sequence
from typing import Any


class IbkrExtraNotInstalled(RuntimeError):
    """Raised when the IBKR live path is used without the ``ibkr`` extra installed."""

    def __init__(self) -> None:
        super().__init__(
            "The IBKR live path needs the `ibkr` extra (Nautilus's InteractiveBrokers "
            "adapter). Install it with `uv sync --extra ibkr` (pulls nautilus-trader[ib]); "
            "a live connect also needs a running TWS or IB Gateway."
        )


def build_data_client_config(
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    client_id: int = 1,
    delayed: bool = False,
    load_instrument_ids: Sequence[str] = (),
) -> Any:
    """Construct an ``InteractiveBrokersDataClientConfig`` for a live ``TradingNode``.

    ``port`` defaults follow IB convention when ``None`` (IB Gateway 4002 paper / 4001 live,
    TWS 7497 paper / 7496 live) — left to the adapter. ``delayed`` selects delayed market
    data (entitlement-free), otherwise real-time. ``load_instrument_ids`` are the Nautilus
    instrument-id strings the provider should load on start.

    Raises :class:`IbkrExtraNotInstalled` if the ``ibkr`` extra is not installed, so callers
    on a gate machine get an actionable message instead of an opaque ``ModuleNotFoundError``.
    """
    try:
        from ibapi.common import MarketDataTypeEnum
        from nautilus_trader.adapters.interactive_brokers.config import (
            InteractiveBrokersDataClientConfig,
            InteractiveBrokersInstrumentProviderConfig,
        )
    except ModuleNotFoundError as exc:  # the `ibkr` extra (ibapi) is absent
        raise IbkrExtraNotInstalled() from exc

    market_data_type = MarketDataTypeEnum.DELAYED if delayed else MarketDataTypeEnum.REALTIME
    provider = InteractiveBrokersInstrumentProviderConfig(
        load_ids=frozenset(load_instrument_ids),
    )
    return InteractiveBrokersDataClientConfig(
        ibg_host=host,
        ibg_port=port,
        ibg_client_id=client_id,
        market_data_type=market_data_type,
        instrument_provider=provider,
    )
