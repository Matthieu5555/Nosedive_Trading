"""IBKR connectivity — two ingestion paths (ADR 0023/0024/0025).

- **Client Portal REST/WS** (ADR 0024, preferred): :class:`CpRestTransport` +
  :class:`CpRestSession` (the ``/tickle`` keepalive). The course-required REST path.
- **Nautilus TWS** (ADR 0025, manual-flip fallback): :func:`build_data_client_config`,
  import-guarded on the ``ibkr`` extra.

:func:`select_ibkr_transport` picks one by config. The hand-rolled ``ib_async`` ``IbkrTransport``
is **superseded** — kept as a file until C5, reached only by direct import, not surfaced here.
"""

from .cp_rest_session import CpRestSession
from .cp_rest_transport import CpRestTransport, CpRestTransportError
from .ibkr_transport_choice import DEFAULT_IBKR_TRANSPORT, IbkrTransport, select_ibkr_transport
from .nautilus_ibkr import IbkrExtraNotInstalled, build_data_client_config

__all__ = [
    # Nautilus-TWS path (ADR 0025)
    "IbkrExtraNotInstalled",
    "build_data_client_config",
    # Client Portal REST path (ADR 0024)
    "CpRestTransport",
    "CpRestTransportError",
    "CpRestSession",
    # Path selector (ADR 0024 §2)
    "IbkrTransport",
    "DEFAULT_IBKR_TRANSPORT",
    "select_ibkr_transport",
]
