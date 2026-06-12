"""Re-export of the shared WebSocket-listener lifecycle.

The canonical implementation lives in :mod:`algotrading.infra.collectors.ws_listener`
(owned thread, stop event, reconnect with backoff, fault callback). This module is kept so
the leaf's import path stays stable; it adds nothing.
"""

from __future__ import annotations

from algotrading.infra.collectors.ws_listener import WebSocketListener

__all__ = ["WebSocketListener"]
