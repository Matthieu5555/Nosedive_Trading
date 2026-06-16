from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..connectivity.cp_rest_transport import SupportsRestGet
from .cp_rest_wire import SNAPSHOT_FIELD_TAGS, SnapshotRow, parse_snapshot_rows

SNAPSHOT_PATH = "/iserver/marketdata/snapshot"

_WARMUP_ATTEMPTS = 8
_WARMUP_SLEEP_S = 1.0


@dataclass(frozen=True, slots=True)
class WarmupConfig:

    attempts: int = _WARMUP_ATTEMPTS
    sleep_s: float = _WARMUP_SLEEP_S
    skip_dead_after: int | None = 2

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError(f"warm-up attempts must be >= 1, got {self.attempts}")
        if self.sleep_s < 0:
            raise ValueError(f"warm-up sleep_s must be >= 0, got {self.sleep_s}")
        if self.skip_dead_after is not None and self.skip_dead_after < 1:
            raise ValueError(
                f"skip_dead_after must be >= 1 or None, got {self.skip_dead_after}"
            )


DEFAULT_WARMUP = WarmupConfig()

SNAPSHOT_MAX_CONIDS = 50


def _populated_conids(rows: Sequence[SnapshotRow], requested: frozenset[int]) -> set[int]:
    return {
        row.conid
        for row in rows
        if row.conid is not None and row.conid in requested and row.has_market_value()
    }


def _warmup_poll_batch(
    transport: SupportsRestGet,
    batch: Sequence[int],
    sleep: Callable[[float], None],
    config: WarmupConfig,
) -> tuple[SnapshotRow, ...]:
    requested = frozenset(batch)
    params = {
        "conids": ",".join(str(conid) for conid in sorted(requested)),
        "fields": ",".join(SNAPSHOT_FIELD_TAGS),
    }

    def _poll() -> tuple[SnapshotRow, ...]:
        return parse_snapshot_rows(transport.get(SNAPSHOT_PATH, params=params))

    rows = _poll()
    populated = _populated_conids(rows, requested)
    stalled_polls = 0
    for _attempt in range(config.attempts - 1):
        if populated == requested:
            break
        sleep(config.sleep_s)
        rows = _poll()
        next_populated = _populated_conids(rows, requested)
        if next_populated and next_populated <= populated:
            break
        if next_populated == populated:
            stalled_polls += 1
            if config.skip_dead_after is not None and stalled_polls >= config.skip_dead_after:
                break
        else:
            stalled_polls = 0
        populated = next_populated
    return rows


def snapshot_with_warmup(
    transport: SupportsRestGet,
    *,
    conids: Sequence[int],
    sleep: Callable[[float], None] | None = None,
    warmup: WarmupConfig | None = None,
) -> tuple[SnapshotRow, ...]:
    resolved_sleep = sleep if sleep is not None else time.sleep
    resolved_warmup = warmup if warmup is not None else DEFAULT_WARMUP
    ordered = sorted(frozenset(conids))
    rows: list[SnapshotRow] = []
    for start in range(0, len(ordered), SNAPSHOT_MAX_CONIDS):
        batch = ordered[start : start + SNAPSHOT_MAX_CONIDS]
        rows.extend(_warmup_poll_batch(transport, batch, resolved_sleep, resolved_warmup))
    return tuple(rows)


def snapshot_index_spot(
    transport: SupportsRestGet,
    conid: int,
    *,
    sleep: Callable[[float], None] | None = None,
    warmup: WarmupConfig | None = None,
) -> float | None:
    for row in snapshot_with_warmup(transport, conids=(conid,), sleep=sleep, warmup=warmup):
        if row.conid != conid:
            continue
        spot = row.spot_value()
        if spot is not None:
            return spot
    return None
