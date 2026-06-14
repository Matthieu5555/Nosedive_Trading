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


@runtime_checkable
class GridPointInput(Protocol):
    """One projected grid cell, as the two grid-aware checks (WS 1H) read it.

    The grid checks validate WS 1F's projected (tenor × delta-band) grid. They need only
    these facts off each cell, so — like :class:`CollectorContinuityInput` — the QC plane
    declares the minimum surface as a structural Protocol rather than importing 1F's
    concrete ``ProjectedOptionAnalytics`` (which satisfies it with no adapter):

    - ``underlying`` — which underlying the cell belongs to (named on a breach);
    - ``tenor_label`` — the pinned tenor the cell projects onto (``10d``…``3y``), the key
      the coverage floor and the band check group by;
    - ``target_delta`` — the **signed band-axis** delta the cell was solved for (``-0.30`` …
      ``0.0`` … ``+0.30``), the measure the Δ-band completeness check spans against the
      configured band edges. This is the band coordinate, **not** the realized greek delta:
      the two ATM pillars sit at ``0.0`` here (the band centre) while their realized deltas
      are ≈ ±0.5, so spanning the realized delta could never enforce a step across ATM —
      the band axis is what defines completeness;
    - ``delta`` — the option's realized signed decimal delta at the solved strike (kept on the
      contract; not what the band check spans).
    """

    @property
    def underlying(self) -> str: ...

    @property
    def tenor_label(self) -> str: ...

    @property
    def target_delta(self) -> float: ...

    @property
    def delta(self) -> float: ...


@runtime_checkable
class IvSpreadInput(Protocol):
    """One put−call IV spread point, as the spread blowout check (ADR 0048) reads it.

    The spread QC needs only where the cell sits and how far the two wings' implied vols
    diverged, so — like :class:`GridPointInput` — the QC plane declares the minimum surface as
    a structural Protocol. ``surfaces.IvSpreadPoint`` satisfies it with no adapter:

    - ``underlying`` — which underlying the cell belongs to (named on a breach);
    - ``tenor_label`` / ``delta_band`` — the cell coordinate, so an operator sees *which* cell
      blew out;
    - ``iv_spread`` — the signed put−call IV spread (``put_iv − call_iv``) at the cell's strike;
      its magnitude is what the check bounds.
    """

    @property
    def underlying(self) -> str: ...

    @property
    def tenor_label(self) -> str: ...

    @property
    def delta_band(self) -> str: ...

    @property
    def iv_spread(self) -> float: ...
