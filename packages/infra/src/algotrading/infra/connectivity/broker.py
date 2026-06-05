"""The broker-agnostic seam, re-exported from the frozen contract.

The tick type and session protocol the whole plane speaks live in
:mod:`algotrading.infra.contracts` — they are the frozen seam M0 owns. This module
re-exports them under the historical ``connectivity.broker`` path so the supervisor,
the collector, and the fakes keep importing from one place, while there is exactly
**one** definition of :class:`BrokerTick`/:class:`BrokerSession` in the workspace.

A concrete broker — a live IBKR session, the in-memory fake, or the disk replay —
hides everything broker-shaped behind :class:`BrokerSession`. The broker's native
tick-type enum is mapped to the plain string ``field_name`` *inside the adapter*, so
no broker enum ever crosses this line; that is what lets replay emit the very same
:class:`BrokerTick` the live adapter does and run the same collector code over it.
"""

from __future__ import annotations

from algotrading.infra.contracts import BrokerSession, BrokerTick, content_event_id

__all__ = ["BrokerSession", "BrokerTick", "content_event_id"]
