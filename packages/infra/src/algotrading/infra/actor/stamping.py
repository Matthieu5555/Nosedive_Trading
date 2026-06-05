"""Build provenance stamps at the actor's emission boundary, clock injected.

This is the one place the actor turns "which source records fed this, and when
was it computed" into A's :class:`provenance.ProvenanceStamp`. It exists because
two of D's adapters (``risk_aggregate``, ``scenario_result``) and C's
``pricing_result`` take a *pre-built* stamp rather than a ``calc_ts`` — so the
actor must construct those stamps itself. The other C adapters
(``forward_curve_point``, ``iv_point``, ``surface_parameters``,
``surface_grid_cells``) and ``build_snapshots`` take ``calc_ts``/``config_hash``
and build their own stamps internally; the actor passes them the same injected
``calc_ts`` so every output on a run shares one computation time.

The discipline this enforces is the whole reason replay is byte-identical: the
``calc_ts`` is *injected*, never read from a clock here. Feed two runs the same
``calc_ts`` and the same sources and the stamps are identical down to the hash.
``build_stamp`` is a thin, ordering-free wrapper over :func:`provenance.stamp`,
which already canonicalizes and sorts its sources, so the caller may pass sources
in any order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp


@dataclass(frozen=True, slots=True)
class StampSource:
    """One source record a derived value was built from, with its event time.

    ``table`` and ``primary_key`` name the row exactly (the full key, in registry
    key order — see :func:`provenance.source_ref`); ``source_ts`` is that row's
    own timestamp, carried so the stamp records *when* the inputs happened, not
    only which they were. Pass the key fields as the typed values; canonicalization
    to strings happens inside :func:`provenance.source_ref`.
    """

    table: str
    primary_key: tuple[object, ...]
    source_ts: datetime


def build_stamp(
    *,
    calc_ts: datetime,
    code_version: str,
    config_hash: str,
    sources: Sequence[StampSource],
) -> ProvenanceStamp:
    """Assemble a provenance stamp from injected ``calc_ts`` and the source rows.

    ``code_version`` is the producing module's version constant (e.g.
    ``PRICER_VERSION`` for a pricing result, ``RISK_ENGINE_VERSION`` for a risk
    aggregate or scenario). ``sources`` may be given in any order; the underlying
    :func:`provenance.stamp` sorts them into canonical order before hashing, so the
    resulting stamp does not depend on caller order. An empty ``sources`` is
    allowed by the stamp machinery but is almost always a wiring bug for a derived
    record, so callers should pass the real lineage.
    """
    refs = tuple(source_ref(item.table, *item.primary_key) for item in sources)
    timestamps = tuple(item.source_ts for item in sources)
    return stamp(
        calc_ts=calc_ts,
        code_version=code_version,
        config_hash=config_hash,
        source_records=refs,
        source_timestamps=timestamps,
    )
