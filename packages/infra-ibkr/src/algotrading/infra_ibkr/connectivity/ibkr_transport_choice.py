from enum import StrEnum


class IbkrTransport(StrEnum):

    REST = "rest"
    NAUTILUS_TWS = "nautilus-tws"


DEFAULT_IBKR_TRANSPORT = IbkrTransport.REST


def select_ibkr_transport(choice: str | None = None) -> IbkrTransport:
    if choice is None:
        return DEFAULT_IBKR_TRANSPORT
    try:
        return IbkrTransport(choice)
    except ValueError as exc:
        valid = ", ".join(transport.value for transport in IbkrTransport)
        raise ValueError(f"unknown IBKR transport {choice!r}; expected one of: {valid}") from exc
