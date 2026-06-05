"""Config selector for the IBKR ingestion path (ADR 0024).

IBKR has two ingestion paths that both normalize into ``RawMarketEvent``: the Client Portal
**REST/WS** adapter (this leaf's ``cp_rest_*``) and Nautilus's shipped **TWS** adapter
(``nautilus_ibkr``). Per ADR 0024 §2 the choice is made by config — **REST is preferred**, TWS is
the manual-flip fallback; there is no automatic failover. The live wiring that consumes this
(building the actual collector / ``TradingNode``) is a later task; this records the seam.
"""

from enum import StrEnum


class IbkrTransport(StrEnum):
    """The two IBKR ingestion paths."""

    REST = "rest"
    NAUTILUS_TWS = "nautilus-tws"


DEFAULT_IBKR_TRANSPORT = IbkrTransport.REST


def select_ibkr_transport(choice: str | None = None) -> IbkrTransport:
    """Resolve a config string to an :class:`IbkrTransport`; defaults to REST (ADR 0024 §2)."""
    if choice is None:
        return DEFAULT_IBKR_TRANSPORT
    try:
        return IbkrTransport(choice)
    except ValueError as exc:
        valid = ", ".join(transport.value for transport in IbkrTransport)
        raise ValueError(f"unknown IBKR transport {choice!r}; expected one of: {valid}") from exc
