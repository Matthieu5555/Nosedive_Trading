"""Errors raised by the collector."""

from __future__ import annotations


class CollectorError(Exception):
    """Base class for all collector-layer failures."""


class ReservedFieldError(CollectorError):
    """A broker tick used a field name in the reserved meta-event namespace.

    Field names beginning with ``__`` are reserved for the collector's own meta-events
    (a recorded gap), so a real observation may never use one — otherwise an
    observation could be mistaken for a gap, or collide with one in the append-only
    store. A tick carrying such a field is rejected rather than silently stored.
    """

    def __init__(self, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(
            f"field name {field_name!r} uses the reserved '__' meta-event namespace"
        )
