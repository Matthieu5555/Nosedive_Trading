from __future__ import annotations

from collections.abc import Mapping

from algotrading.core.hashing import canonical_dumps, sha256_hex

_SHORT_HASH_LENGTH = 12


def dedup_preserving_order(values: tuple[float, ...]) -> tuple[float, ...]:
    seen: set[float] = set()
    unique: list[float] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return tuple(unique)


def short_construction_hash(
    payload: Mapping[str, object], *, length: int = _SHORT_HASH_LENGTH
) -> str:
    return sha256_hex(canonical_dumps(payload))[:length]
