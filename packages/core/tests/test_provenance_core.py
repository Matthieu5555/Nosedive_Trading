"""Provenance stamp: build/validate, order-independence, and the bake-off survival.

The stamp is the determinism + lineage mechanism every derived record carries. These
tests pin the three behaviours M1/M2/M3/M4 rely on: a stamp built by ``stamp`` always
validates, source order never changes the hash, and a tampered stamp is rejected.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from algotrading.core import (
    ProvenanceError,
    ProvenanceValidationError,
    code_version,
    source_ref,
    stamp,
    validate_stamp,
)

_CALC = datetime(2026, 6, 5, 14, 30, tzinfo=UTC)
_T1 = datetime(2026, 6, 5, 9, 0, tzinfo=UTC)
_T2 = datetime(2026, 6, 5, 9, 1, tzinfo=UTC)


def _two_source_stamp(order: str):
    refs = (
        source_ref("raw_market_events", "sess-1", "evt-a"),
        source_ref("raw_market_events", "sess-1", "evt-b"),
    )
    times = (_T1, _T2)
    if order == "reversed":
        refs = tuple(reversed(refs))
        times = tuple(reversed(times))
    return stamp(
        calc_ts=_CALC,
        code_version="algotrading-infra-0.1.0",
        config_hash="cfg-abc",
        source_records=refs,
        source_timestamps=times,
    )


def test_stamp_built_by_stamp_always_validates() -> None:
    # The bake-off survival case: a stamp produced by the merged `stamp` validates.
    s = _two_source_stamp("forward")
    validate_stamp(s)  # raises on failure; reaching here is the assertion


def test_stamp_hash_is_independent_of_source_order() -> None:
    # Order of arrival is plumbing, not result: the two stamps must be byte-identical.
    assert _two_source_stamp("forward").stamp_hash == _two_source_stamp("reversed").stamp_hash


def test_naive_calc_ts_is_refused() -> None:
    with pytest.raises(ProvenanceError):
        stamp(
            calc_ts=datetime(2026, 6, 5, 14, 30),  # naive
            code_version="v",
            config_hash="c",
            source_records=(source_ref("raw_market_events", "s", "e"),),
            source_timestamps=(_T1,),
        )


def test_tampered_hash_is_rejected() -> None:
    import dataclasses

    s = _two_source_stamp("forward")
    tampered = dataclasses.replace(s, stamp_hash="0" * 64)
    with pytest.raises(ProvenanceValidationError):
        validate_stamp(tampered)


def test_tampered_field_breaks_validation() -> None:
    import dataclasses

    s = _two_source_stamp("forward")
    # Same stored hash, mutated config — the recomputed hash no longer matches.
    tampered = dataclasses.replace(s, config_hash="cfg-OTHER")
    with pytest.raises(ProvenanceValidationError):
        validate_stamp(tampered)


def test_code_version_unknown_distribution_falls_back() -> None:
    assert code_version("no-such-distribution-xyz") == "0.0.0+unknown"


def test_code_version_reads_installed_distribution() -> None:
    # algotrading-core is installed editable in the gate env; its version is real.
    assert code_version("algotrading-core") == "0.1.0"


def test_stamp_hash_is_stable_across_processes() -> None:
    # Cross-process stability (TESTING.md): the hash must not depend on PYTHONHASHSEED.
    expected = _two_source_stamp("forward").stamp_hash
    code = (
        "from datetime import UTC, datetime;"
        "from algotrading.core import stamp, source_ref;"
        "print(stamp(calc_ts=datetime(2026,6,5,14,30,tzinfo=UTC),"
        "code_version='algotrading-infra-0.1.0', config_hash='cfg-abc',"
        "source_records=(source_ref('raw_market_events','sess-1','evt-a'),"
        "source_ref('raw_market_events','sess-1','evt-b')),"
        "source_timestamps=(datetime(2026,6,5,9,0,tzinfo=UTC),"
        "datetime(2026,6,5,9,1,tzinfo=UTC))).stamp_hash)"
    )
    for seed in ("0", "1", "12345"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert out.stdout.strip() == expected, f"hash drifted under PYTHONHASHSEED={seed}"
