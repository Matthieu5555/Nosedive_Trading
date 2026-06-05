"""IBKR connectivity (ADR 0023/0024).

Connectivity is Nautilus's InteractiveBrokers data client; build its config via
:func:`build_data_client_config` (import-guarded on the ``ibkr`` extra). The hand-rolled
``ib_async`` ``IbkrTransport`` is **superseded** — kept as a file until C5 removes it,
reached only by direct import, and not surfaced here so this package imports without the SDK.
"""

from .nautilus_ibkr import IbkrExtraNotInstalled, build_data_client_config

__all__ = ["IbkrExtraNotInstalled", "build_data_client_config"]
