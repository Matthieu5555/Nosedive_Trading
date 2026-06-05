"""The QC plane's declared inputs that no current contract or producer type supplies.

Most checks consume a typed object another module already emits (a ``ForwardEstimate``,
a ``SliceFit``, a ``PositionRisk``). The collector-continuity check is the exception:
the market-data plane's session summary is not a persisted contract, and the merged
``algotrading.infra.collectors`` package (C1's domain) does not yet export a summary
type. Rather than reach into a shape C1 has not frozen — or invent a parallel persisted
record — the QC plane declares the *minimum surface* it needs as a structural
:class:`~typing.Protocol`. Any object carrying these four attributes satisfies it: the
market-data plane's eventual ``CollectorSummary``, or a test fixture, with no adapter.

This keeps the check pure and importable on its own, and keeps the seam one-directional
— the QC plane states what it reads; the collector plane is free to add fields around it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CollectorContinuityInput(Protocol):
    """The collector-session facts the continuity check needs to render a verdict.

    ``session_id`` names the session in the result (the offending object on a fail);
    ``gap_count`` is the number of feed-gap events; ``subscribed_count`` /
    ``covered_count`` give the coverage fraction. The check derives coverage as
    ``covered_count / subscribed_count`` (1.0 when nothing was subscribed), so a
    producer supplies the raw counts, not a pre-computed ratio.
    """

    @property
    def session_id(self) -> str: ...

    @property
    def gap_count(self) -> int: ...

    @property
    def subscribed_count(self) -> int: ...

    @property
    def covered_count(self) -> int: ...
