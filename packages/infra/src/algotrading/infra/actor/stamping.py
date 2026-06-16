from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp


@dataclass(frozen=True, slots=True)
class StampSource:

    table: str
    primary_key: tuple[object, ...]
    source_ts: datetime


def build_stamp(
    *,
    calc_ts: datetime,
    code_version: str,
    config_hashes: Mapping[str, str],
    sources: Sequence[StampSource],
) -> ProvenanceStamp:
    refs = tuple(source_ref(item.table, *item.primary_key) for item in sources)
    timestamps = tuple(item.source_ts for item in sources)
    return stamp(
        calc_ts=calc_ts,
        code_version=code_version,
        config_hashes=config_hashes,
        source_records=refs,
        source_timestamps=timestamps,
    )
