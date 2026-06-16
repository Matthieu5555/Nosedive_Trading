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
        config_hashes={"cfg": "cfg-abc"},
        source_records=refs,
        source_timestamps=times,
    )


def test_stamp_built_by_stamp_always_validates() -> None:
    s = _two_source_stamp("forward")
    validate_stamp(s)


def test_stamp_hash_is_independent_of_source_order() -> None:
    assert _two_source_stamp("forward").stamp_hash == _two_source_stamp("reversed").stamp_hash


def test_naive_calc_ts_is_refused() -> None:
    with pytest.raises(ProvenanceError):
        stamp(
            calc_ts=datetime(2026, 6, 5, 14, 30),
            code_version="v",
            config_hashes={"cfg": "c"},
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
    tampered = dataclasses.replace(s, config_hashes={"cfg": "cfg-OTHER"})
    with pytest.raises(ProvenanceValidationError):
        validate_stamp(tampered)


def test_code_version_unknown_distribution_falls_back() -> None:
    assert code_version("no-such-distribution-xyz") == "0.0.0+unknown"


def test_code_version_reads_installed_distribution() -> None:
    assert code_version("algotrading-core") == "0.1.0"


def test_code_identity_returns_a_non_empty_label() -> None:
    from algotrading.core import code_identity

    identity = code_identity()
    assert isinstance(identity, str) and identity
    assert identity == "unknown" or len(identity.removesuffix("-dirty")) >= 7


def test_stamp_hash_is_stable_across_processes() -> None:
    expected = _two_source_stamp("forward").stamp_hash
    code = (
        "from datetime import UTC, datetime;"
        "from algotrading.core import stamp, source_ref;"
        "print(stamp(calc_ts=datetime(2026,6,5,14,30,tzinfo=UTC),"
        "code_version='algotrading-infra-0.1.0', config_hashes={'cfg': 'cfg-abc'},"
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


def test_stamp_hash_matches_the_pinned_golden_digest() -> None:
    calc = datetime(2026, 6, 1, 16, 30, tzinfo=UTC)
    src = datetime(2026, 6, 1, 16, 0, tzinfo=UTC)
    refs = (
        source_ref("iv_points", src, "ESM6 C5300"),
        source_ref("iv_points", src, "ESM6 P5300"),
    )
    pinned = stamp(
        calc_ts=calc,
        code_version="pin-1.0.0",
        config_hashes={"pricing": "abc123", "qc": "def456"},
        source_records=refs,
        source_timestamps=(src, src),
    )
    assert pinned.stamp_hash == (
        "2d228ad44515e67cbe6006c4a80f835f5d14c90d7aa7ad5e8d9913630a40e9e0"
    )


def _stamp_with(as_of):
    return stamp(
        calc_ts=_CALC,
        code_version="algotrading-infra-0.1.0",
        config_hashes={"pricing": "p-1"},
        source_records=(source_ref("iv_points", "k-1"),),
        source_timestamps=(_T1,),
        as_of=as_of,
    )


def test_as_of_is_recorded_on_the_stamp_and_validates() -> None:
    from datetime import date

    replayed = date(2026, 6, 10)
    s = _stamp_with(replayed)
    assert s.as_of == replayed
    validate_stamp(s)


def test_as_of_folds_into_the_stamp_hash_only_when_set() -> None:
    from datetime import date

    current = _stamp_with(None)
    dated = _stamp_with(date(2026, 6, 10))
    other_day = _stamp_with(date(2026, 6, 11))

    assert current.as_of is None
    assert dated.stamp_hash != current.stamp_hash
    assert dated.stamp_hash != other_day.stamp_hash
    default = stamp(
        calc_ts=_CALC,
        code_version="algotrading-infra-0.1.0",
        config_hashes={"pricing": "p-1"},
        source_records=(source_ref("iv_points", "k-1"),),
        source_timestamps=(_T1,),
    )
    assert current.stamp_hash == default.stamp_hash


def test_tampered_as_of_breaks_validation() -> None:
    import dataclasses
    from datetime import date

    s = _stamp_with(date(2026, 6, 10))
    tampered = dataclasses.replace(s, as_of=date(2026, 6, 11))
    with pytest.raises(ProvenanceValidationError):
        validate_stamp(tampered)


def test_snapshot_stamp_equals_stamp_with_the_timestamp_repeated_per_source() -> None:
    from algotrading.core import snapshot_stamp

    src = datetime(2026, 6, 5, 9, 0, tzinfo=UTC)
    refs = (
        source_ref("iv_points", src, "k-2"),
        source_ref("iv_points", src, "k-1"),
    )
    via_helper = snapshot_stamp(
        calc_ts=_CALC,
        code_version="algotrading-infra-0.1.0",
        config_hashes={"cfg": "cfg-abc"},
        source_snapshot_ts=src,
        source_records=refs,
    )
    by_hand = stamp(
        calc_ts=_CALC,
        code_version="algotrading-infra-0.1.0",
        config_hashes={"cfg": "cfg-abc"},
        source_records=refs,
        source_timestamps=(src, src),
    )
    assert via_helper == by_hand
    assert via_helper.stamp_hash == by_hand.stamp_hash
    validate_stamp(via_helper)


def test_snapshot_stamp_with_no_sources_carries_no_timestamps() -> None:
    from algotrading.core import snapshot_stamp

    src = datetime(2026, 6, 5, 9, 0, tzinfo=UTC)
    empty = snapshot_stamp(
        calc_ts=_CALC,
        code_version="v",
        config_hashes={"cfg": "h"},
        source_snapshot_ts=src,
        source_records=(),
    )
    assert empty.source_records == ()
    assert empty.source_timestamps == ()
