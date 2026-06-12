"""The Saxo leaf's ws_listener module is a thin re-export of the canonical infra class.

The behavior suite for the listener itself lives in infra (`tests/test_ws_listener.py`)
against ``algotrading.infra.collectors.ws_listener`` — the single home the byte-identical
leaf twins were hoisted to.
"""

from __future__ import annotations

from algotrading.infra.collectors.ws_listener import WebSocketListener
from algotrading.infra_saxo.connectivity.ws_listener import (
    WebSocketListener as SaxoWebSocketListener,
)


def test_leaf_import_path_reexports_the_one_shared_listener() -> None:
    assert SaxoWebSocketListener is WebSocketListener
