"""Provenance stamps: full shape, determinism, and order-independence.

Determinism here is a mechanism, not a hope: the same inputs must give the same
stamp, and the *order* of the source records must not matter. We assert both on
worked examples and, for order-independence, as a property over many generated
inputs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from provenance import ProvenanceError, stamp

CALC = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 5, 31, 9, 30, tzinfo=UTC)
T2 = datetime(2026, 5, 31, 9, 31, tzinfo=UTC)

SRC = Path(__file__).resolve().parents[1] / "src"


def test_stamp_carries_every_required_field() -> None:
    result = stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_record_ids=("e2", "e1"),
        source_timestamps=(T2, T1),
    )
    assert result.calc_ts == CALC
    assert result.code_version == "v1"
    assert result.config_hash == "cfg"
    # Stored in canonical (sorted) order regardless of input order.
    assert result.source_record_ids == ("e1", "e2")
    assert result.source_timestamps == (T1, T2)
    # The content hash is a real SHA-256 hex digest.
    assert len(result.stamp_hash) == 64
    int(result.stamp_hash, 16)


def test_identical_inputs_produce_identical_stamp() -> None:
    first = stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_record_ids=("e1", "e2"),
        source_timestamps=(T1, T2),
    )
    second = stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_record_ids=("e1", "e2"),
        source_timestamps=(T1, T2),
    )
    assert first == second


def test_reordering_sources_yields_an_identical_stamp() -> None:
    in_order = stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_record_ids=("e1", "e2"),
        source_timestamps=(T1, T2),
    )
    reversed_order = stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_record_ids=("e2", "e1"),
        source_timestamps=(T2, T1),
    )
    assert in_order == reversed_order
    assert in_order.stamp_hash == reversed_order.stamp_hash


def test_naive_calc_timestamp_is_rejected() -> None:
    with pytest.raises(ProvenanceError):
        stamp(
            calc_ts=datetime(2026, 5, 31, 12, 0),  # naive: no tzinfo
            code_version="v1",
            config_hash="cfg",
            source_record_ids=("e1",),
            source_timestamps=(T1,),
        )


def test_naive_source_timestamp_is_rejected() -> None:
    with pytest.raises(ProvenanceError):
        stamp(
            calc_ts=CALC,
            code_version="v1",
            config_hash="cfg",
            source_record_ids=("e1",),
            source_timestamps=(datetime(2026, 5, 31, 9, 30),),  # naive
        )


@given(ids=st.lists(st.text(min_size=1, max_size=12), min_size=0, max_size=8, unique=True))
def test_stamp_hash_is_invariant_to_source_order(ids: list[str]) -> None:
    # Property: any permutation of the source ids gives the same content hash.
    forward = stamp(
        calc_ts=CALC,
        code_version="v",
        config_hash="h",
        source_record_ids=tuple(ids),
        source_timestamps=(),
    )
    backward = stamp(
        calc_ts=CALC,
        code_version="v",
        config_hash="h",
        source_record_ids=tuple(reversed(ids)),
        source_timestamps=(),
    )
    assert forward.stamp_hash == backward.stamp_hash


# Rebuilds the identical stamp in a fresh interpreter and prints its content hash.
# Inputs are passed out of sorted order on purpose: the stamp's canonicalization,
# not the caller's ordering, is what must make the hash reproducible.
_SUBPROCESS_SCRIPT = """
from datetime import UTC, datetime
from provenance import stamp
s = stamp(
    calc_ts=datetime(2026, 5, 31, 12, 0, tzinfo=UTC),
    code_version="v1",
    config_hash="cfg",
    source_record_ids=("e2", "e1", "e3"),
    source_timestamps=(
        datetime(2026, 5, 31, 9, 31, tzinfo=UTC),
        datetime(2026, 5, 31, 9, 30, tzinfo=UTC),
    ),
)
print(s.stamp_hash)
"""


def _stamp_hash_in_subprocess(hashseed: str) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hashseed
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return result.stdout.strip()


def test_stamp_hash_is_stable_across_processes_and_hash_seeds() -> None:
    # Oracle: two independent processes under different hash seeds must agree with
    # each other and with the in-process stamp built from the same (unsorted)
    # inputs. This catches the classic bug of hashing a dict/set under hash
    # randomization, which passes in-process and silently drifts between runs.
    seed_one = _stamp_hash_in_subprocess("1")
    seed_two = _stamp_hash_in_subprocess("2")
    in_process = stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_record_ids=("e2", "e1", "e3"),
        source_timestamps=(T2, T1),
    ).stamp_hash
    assert seed_one == seed_two == in_process
    assert len(seed_one) == 64
    int(seed_one, 16)
