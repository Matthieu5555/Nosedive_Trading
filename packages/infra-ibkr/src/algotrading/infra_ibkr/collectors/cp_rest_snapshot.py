"""Shared CP REST market-data snapshot engine: URI-safe batching + cold-snapshot warm-up.

``GET /iserver/marketdata/snapshot`` has two operational quirks every caller must survive, and
this module is their single home (extracted from the close capture so the live adapter inherits
the same fixes instead of re-rolling a bare single-shot request):

* **Cold snapshots.** The endpoint returns only field *metadata* (server ids, request echo) on
  the first call(s) for a freshly-subscribed conid; the requested value tags (last/bid/ask/…)
  populate only once the server-side market-data line warms — typically a second or two later.
  A single un-retried call is exactly why a cold capture saw ``spot=None`` and then selected
  zero options. So the same request is polled until the values appear, bounded by
  ``_WARMUP_ATTEMPTS`` (an illiquid contract that never prints cannot hang the fire) and stopped
  early once the populated set stops growing (converged — the dead wings won't print).
* **URI overflow (HTTP 414).** The snapshot is a GET carrying the conids in the query string; a
  full index chain is hundreds of contracts, which overflows the gateway's request-URI limit
  (the failure a real ESTX50 capture hit). Requests are split into ``SNAPSHOT_MAX_CONIDS``-sized
  batches and the rows concatenated; each batch is independently warm-up polled.

Rows come back as validated :class:`~.cp_rest_wire.SnapshotRow` models; "warm" is the
normalizer's own parse (:meth:`SnapshotRow.has_market_value`), so a sentinel-only row counts
cold — it would yield zero events. ``sleep`` is injectable for tests; the default is the wall
clock.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from ..connectivity.cp_rest_transport import SupportsRestGet
from .cp_rest_wire import SNAPSHOT_FIELD_TAGS, SnapshotRow, parse_snapshot_rows

SNAPSHOT_PATH = "/iserver/marketdata/snapshot"

# Warm-up poll budget: bounded attempts, short sleeps (see the module docstring).
_WARMUP_ATTEMPTS = 8
_WARMUP_SLEEP_S = 1.0

# 50 conids ≈ a 600-char URL, comfortably under the gateway's URI limit and well within IBKR's
# documented per-request conid cap.
SNAPSHOT_MAX_CONIDS = 50


def _populated_conids(rows: Sequence[SnapshotRow], requested: frozenset[int]) -> set[int]:
    """The subset of ``requested`` conids whose snapshot row carries a parseable value tag."""
    return {
        row.conid
        for row in rows
        if row.conid is not None and row.conid in requested and row.has_market_value()
    }


def _warmup_poll_batch(
    transport: SupportsRestGet, batch: Sequence[int], sleep: Callable[[float], None]
) -> tuple[SnapshotRow, ...]:
    """Warm-up poll ONE URI-safe batch of conids; return its snapshot rows (possibly empty).

    Issues the same snapshot request up to ``_WARMUP_ATTEMPTS`` times, returning as soon as
    every conid in the batch carries a value tag (fully warm) or the populated set stops
    growing between two polls (converged — the rest are illiquid and won't print). On a gateway
    that already returns values on the first call this returns immediately with a single
    request and no sleep; on a cold subscription it pays a few short polls so the caller sees
    real marks instead of an empty first response.
    """
    requested = frozenset(batch)
    params = {
        "conids": ",".join(str(conid) for conid in sorted(requested)),
        "fields": ",".join(SNAPSHOT_FIELD_TAGS),
    }
    rows = parse_snapshot_rows(transport.get(SNAPSHOT_PATH, params=params))
    populated = _populated_conids(rows, requested)
    for _attempt in range(_WARMUP_ATTEMPTS - 1):
        if populated == requested:
            break  # every requested conid is warm — nothing left to wait for
        sleep(_WARMUP_SLEEP_S)
        rows = parse_snapshot_rows(transport.get(SNAPSHOT_PATH, params=params))
        next_populated = _populated_conids(rows, requested)
        if next_populated and next_populated <= populated:
            break  # no new conid warmed since the last poll — converged, stop polling
        populated = next_populated
    return rows


def snapshot_with_warmup(
    transport: SupportsRestGet,
    *,
    conids: Sequence[int],
    sleep: Callable[[float], None] | None = None,
) -> tuple[SnapshotRow, ...]:
    """Snapshot the conids in URI-safe batches (each warm-up polled) and concatenate the rows.

    Deterministic order: conids are de-duplicated, sorted, then batched. ``sleep`` defaults to
    the wall clock and is injectable so tests drive the warm-up with no real waiting.
    """
    resolved_sleep = sleep if sleep is not None else time.sleep
    ordered = sorted(frozenset(conids))
    rows: list[SnapshotRow] = []
    for start in range(0, len(ordered), SNAPSHOT_MAX_CONIDS):
        batch = ordered[start : start + SNAPSHOT_MAX_CONIDS]
        rows.extend(_warmup_poll_batch(transport, batch, resolved_sleep))
    return tuple(rows)


def snapshot_index_spot(
    transport: SupportsRestGet, conid: int, *, sleep: Callable[[float], None] | None = None
) -> float | None:
    """REST snapshot the index level (last, else bid/ask) to centre a chain window.

    Used only to centre the discovery strike window — a request-shaping number, not an
    observation persisted anywhere. Warm-up polled like every snapshot: the index's first cold
    snapshot carries no value tag, so a single call would return ``spot=None`` and collapse the
    downstream selection. ``None`` when the row is absent or unparseable, in which case the
    chain planner falls back to its spot-less (median-strike) window.
    """
    for row in snapshot_with_warmup(transport, conids=(conid,), sleep=sleep):
        if row.conid != conid:
            continue
        spot = row.spot_value()
        if spot is not None:
            return spot
    return None
