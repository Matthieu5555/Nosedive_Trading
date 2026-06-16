from collections.abc import Sequence
from typing import Any


class IbkrExtraNotInstalled(RuntimeError):

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
    try:
        from ibapi.common import MarketDataTypeEnum
        from nautilus_trader.adapters.interactive_brokers.config import (
            InteractiveBrokersDataClientConfig,
            InteractiveBrokersInstrumentProviderConfig,
        )
    except ModuleNotFoundError as exc:
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
