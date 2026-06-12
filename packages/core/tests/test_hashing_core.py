"""core.hashing — the canonical-JSON + SHA-256 primitives (M25).

Expected values are derived independently of the code under test: the canonical JSON
strings are spelled out as literals (sorted keys, compact separators — the documented
encoding), and the SHA-256 digests come from FIPS 180-2 / RFC 6234 published test
vectors or from hashing those literals with hashlib directly in the test.
"""

from __future__ import annotations

import hashlib
import math

import pytest
from algotrading.core.hashing import canonical_dumps, sha256_hex

# --- sha256_hex: published test vectors --------------------------------------------
# FIPS 180-2 / RFC 6234: SHA-256("") and SHA-256("abc").
_SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", _SHA256_EMPTY), ("abc", _SHA256_ABC)],
    ids=["empty", "abc"],
)
def test_sha256_hex_matches_the_published_vectors(text: str, expected: str) -> None:
    assert sha256_hex(text) == expected


def test_sha256_hex_encodes_utf8() -> None:
    # Non-ASCII must hash the UTF-8 bytes, not a locale-dependent encoding.
    assert sha256_hex("café") == hashlib.sha256("café".encode()).hexdigest()


# --- canonical_dumps: the bare convention, spelled out as literals ------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"b": 2, "a": 1}, '{"a":1,"b":2}'),
        ({"k": [1, 2.5, None, True]}, '{"k":[1,2.5,null,true]}'),
        ({"outer": {"z": "s", "a": 0.99}}, '{"outer":{"a":0.99,"z":"s"}}'),
        ([], "[]"),
        ({}, "{}"),
    ],
    ids=["sorted_keys", "scalars", "nested_sorted", "empty_list", "empty_mapping"],
)
def test_canonical_dumps_renders_the_documented_literal(value: object, expected: str) -> None:
    assert canonical_dumps(value) == expected


def test_canonical_dumps_is_independent_of_construction_order() -> None:
    forward = {"a": 1, "b": 2, "c": 3}
    reversed_build: dict[str, int] = {}
    for key in ("c", "b", "a"):
        reversed_build[key] = forward[key]
    assert canonical_dumps(forward) == canonical_dumps(reversed_build)


def test_canonical_dumps_keeps_values_verbatim_no_zero_collapse() -> None:
    # The BARE convention serializes -0.0 as-is. The -0.0 collapse belongs to the
    # typed-config convention (core.config.canonical_json) — the two are deliberately
    # distinct named functions (M25: no numeric unification, persisted hashes depend
    # on each staying exactly what it is).
    assert canonical_dumps({"x": -0.0}) == '{"x":-0.0}'
    assert canonical_dumps({"x": 0.0}) == '{"x":0.0}'


def test_the_two_conventions_genuinely_differ_on_signed_zero() -> None:
    # Documents the divergence the audit found, as a pinned fact rather than a trap:
    # bare keeps -0.0, typed-config collapses it.
    from algotrading.core.config import canonical_json

    assert canonical_dumps({"x": -0.0}) != canonical_dumps({"x": 0.0})
    assert canonical_json({"x": -0.0}) == canonical_json({"x": 0.0})


def test_canonical_dumps_allows_nan_like_the_inlined_copies_did() -> None:
    # The bare convention inherits json.dumps' default allow_nan=True (what every
    # inlined copy did); rejecting NaN is the typed-config convention's job.
    assert canonical_dumps([math.nan]) == "[NaN]"
