"""Provenance stamps: full shape, determinism, order-independence, and validation.

Determinism here is a mechanism, not a hope: the same inputs must give the same
stamp, and the *order* of the source records must not matter. We assert both on
worked examples and, for order-independence, as a property over many generated
inputs. Validation is the other half: a stamp that was hand-built or mutated must
be caught, above all when its stored hash no longer matches its contents.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from provenance import (
    ProvenanceError,
    ProvenanceStamp,
    ProvenanceValidationError,
    SourceRecordRef,
    source_ref,
    stamp,
    validate_stamp,
)

CALC = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 5, 31, 9, 30, tzinfo=UTC)
T2 = datetime(2026, 5, 31, 9, 31, tzinfo=UTC)

SRC = Path(__file__).resolve().parents[1] / "src"

# Two source references in a fixed table, used across the determinism examples.
E1 = source_ref("raw_market_events", "sess-1", "evt-1")
E2 = source_ref("raw_market_events", "sess-1", "evt-2")


def _valid_stamp() -> ProvenanceStamp:
    return stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_records=(E1, E2),
        source_timestamps=(T1, T2),
    )


def test_stamp_carries_every_required_field() -> None:
    result = stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_records=(E2, E1),
        source_timestamps=(T2, T1),
    )
    assert result.calc_ts == CALC
    assert result.code_version == "v1"
    assert result.config_hash == "cfg"
    # Stored in canonical (sorted) order regardless of input order.
    assert result.source_records == (E1, E2)
    assert result.source_timestamps == (T1, T2)
    # The content hash is a real SHA-256 hex digest.
    assert len(result.stamp_hash) == 64
    int(result.stamp_hash, 16)


def test_source_ref_keeps_the_full_primary_key() -> None:
    # The reference carries the *whole* key, not one field, so it cannot conflate
    # two raw events that share an event id across different sessions.
    ref = source_ref("raw_market_events", "sess-2", "evt-1")
    assert ref == SourceRecordRef(table="raw_market_events", primary_key=("sess-2", "evt-1"))
    assert ref != source_ref("raw_market_events", "sess-1", "evt-1")


def test_source_ref_canonicalizes_timestamp_key_components() -> None:
    # A datetime key component is reduced to a UTC ISO string so the reference is
    # JSON-serializable and hashes the same regardless of the input zone.
    ts = datetime(2026, 5, 31, 9, 30, tzinfo=UTC)
    assert source_ref("market_state_snapshots", ts, "AAPL").primary_key == (
        "2026-05-31T09:30:00+00:00",
        "AAPL",
    )


def test_identical_inputs_produce_identical_stamp() -> None:
    assert _valid_stamp() == _valid_stamp()


def test_reordering_sources_yields_an_identical_stamp() -> None:
    in_order = stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_records=(E1, E2),
        source_timestamps=(T1, T2),
    )
    reversed_order = stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_records=(E2, E1),
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
            source_records=(E1,),
            source_timestamps=(T1,),
        )


def test_naive_source_timestamp_is_rejected() -> None:
    with pytest.raises(ProvenanceError):
        stamp(
            calc_ts=CALC,
            code_version="v1",
            config_hash="cfg",
            source_records=(E1,),
            source_timestamps=(datetime(2026, 5, 31, 9, 30),),  # naive
        )


@given(ids=st.lists(st.text(min_size=1, max_size=12), min_size=0, max_size=8, unique=True))
def test_stamp_hash_is_invariant_to_source_order(ids: list[str]) -> None:
    # Property: any permutation of the source references gives the same content hash.
    refs = tuple(source_ref("raw_market_events", "sess-1", event_id) for event_id in ids)
    forward = stamp(
        calc_ts=CALC,
        code_version="v",
        config_hash="h",
        source_records=refs,
        source_timestamps=(),
    )
    backward = stamp(
        calc_ts=CALC,
        code_version="v",
        config_hash="h",
        source_records=tuple(reversed(refs)),
        source_timestamps=(),
    )
    assert forward.stamp_hash == backward.stamp_hash


# -- validation -----------------------------------------------------------------


def test_validate_stamp_accepts_a_freshly_built_stamp() -> None:
    validate_stamp(_valid_stamp())  # does not raise


def test_validate_stamp_rejects_a_tampered_hash() -> None:
    tampered = dataclasses.replace(_valid_stamp(), stamp_hash="0" * 64)
    with pytest.raises(ProvenanceValidationError) as info:
        validate_stamp(tampered)
    assert info.value.field == "stamp_hash"


def test_validate_stamp_rejects_a_mutated_field_whose_hash_was_not_updated() -> None:
    # Changing any hashed field without recomputing the hash must be caught — this
    # is what makes the stamp tamper-evident.
    mutated = dataclasses.replace(_valid_stamp(), config_hash="a-different-config")
    with pytest.raises(ProvenanceValidationError) as info:
        validate_stamp(mutated)
    assert info.value.field == "stamp_hash"


def test_validate_stamp_rejects_a_naive_calc_ts() -> None:
    # Hand-built (never through stamp()): a naive calc_ts slips past construction
    # but not validation.
    naive = ProvenanceStamp(
        calc_ts=datetime(2026, 5, 31, 12, 0),
        code_version="v1",
        config_hash="cfg",
        source_records=(E1,),
        source_timestamps=(),
        stamp_hash="x" * 64,
    )
    with pytest.raises(ProvenanceValidationError) as info:
        validate_stamp(naive)
    assert info.value.field == "calc_ts"


@pytest.mark.parametrize(
    ("empty_field", "candidate"),
    [
        (
            "code_version",
            ProvenanceStamp(
                calc_ts=CALC,
                code_version="",
                config_hash="cfg",
                source_records=(E1,),
                source_timestamps=(),
                stamp_hash="x" * 64,
            ),
        ),
        (
            "config_hash",
            ProvenanceStamp(
                calc_ts=CALC,
                code_version="v1",
                config_hash="",
                source_records=(E1,),
                source_timestamps=(),
                stamp_hash="x" * 64,
            ),
        ),
        (
            "stamp_hash",
            ProvenanceStamp(
                calc_ts=CALC,
                code_version="v1",
                config_hash="cfg",
                source_records=(E1,),
                source_timestamps=(),
                stamp_hash="",
            ),
        ),
    ],
)
def test_validate_stamp_rejects_an_empty_required_field(
    empty_field: str, candidate: ProvenanceStamp
) -> None:
    with pytest.raises(ProvenanceValidationError) as info:
        validate_stamp(candidate)
    assert info.value.field == empty_field


def test_validate_stamp_rejects_a_malformed_source_reference() -> None:
    bad_ref = SourceRecordRef(table="", primary_key=("evt-1",))
    built = stamp(
        calc_ts=CALC,
        code_version="v1",
        config_hash="cfg",
        source_records=(bad_ref,),
        source_timestamps=(),
    )
    with pytest.raises(ProvenanceValidationError) as info:
        validate_stamp(built)
    assert info.value.field == "source_records"


# -- cross-process determinism --------------------------------------------------

# Rebuilds the identical stamp in a fresh interpreter and prints its content hash.
# Inputs are passed out of sorted order on purpose: the stamp's canonicalization,
# not the caller's ordering, is what must make the hash reproducible.
_SUBPROCESS_SCRIPT = """
from datetime import UTC, datetime
from provenance import source_ref, stamp
s = stamp(
    calc_ts=datetime(2026, 5, 31, 12, 0, tzinfo=UTC),
    code_version="v1",
    config_hash="cfg",
    source_records=(
        source_ref("raw_market_events", "sess-1", "evt-2"),
        source_ref("raw_market_events", "sess-1", "evt-1"),
        source_ref("raw_market_events", "sess-1", "evt-3"),
    ),
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
        source_records=(
            source_ref("raw_market_events", "sess-1", "evt-2"),
            source_ref("raw_market_events", "sess-1", "evt-1"),
            source_ref("raw_market_events", "sess-1", "evt-3"),
        ),
        source_timestamps=(T2, T1),
    ).stamp_hash
    assert seed_one == seed_two == in_process
    assert len(seed_one) == 64
    int(seed_one, 16)
