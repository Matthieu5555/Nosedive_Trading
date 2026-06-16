from __future__ import annotations

import hashlib
import math

import pytest
from algotrading.core.hashing import canonical_dumps, sha256_hex

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
    assert sha256_hex("café") == hashlib.sha256("café".encode()).hexdigest()


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
    assert canonical_dumps({"x": -0.0}) == '{"x":-0.0}'
    assert canonical_dumps({"x": 0.0}) == '{"x":0.0}'


def test_the_two_conventions_genuinely_differ_on_signed_zero() -> None:
    from algotrading.core.config import canonical_json

    assert canonical_dumps({"x": -0.0}) != canonical_dumps({"x": 0.0})
    assert canonical_json({"x": -0.0}) == canonical_json({"x": 0.0})


def test_canonical_dumps_allows_nan_like_the_inlined_copies_did() -> None:
    assert canonical_dumps([math.nan]) == "[NaN]"
