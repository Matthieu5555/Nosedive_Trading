from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from algotrading.core.config import QcThresholdConfig
from algotrading.infra.contracts import MarketStateSnapshot, RawMarketEvent, validate
from algotrading.infra.snapshots import (
    InsufficientSnapshotData,
    SnapshotContext,
    assess_quote,
    assess_snapshot,
    build_snapshot,
    build_snapshots,
    cross_strike_monotonicity_violations,
    latest_by_field_before,
    resolve_reference_spot,
)
from algotrading.infra.snapshots.quote_quality import (
    check_bid_positive,
    check_crossed_or_locked,
    check_open_interest,
    check_price_against_intrinsic,
    check_quote_age,
    check_spread,
)
from fixtures.events import (
    OPTION,
    SNAPSHOT_TS,
    STALE_THRESHOLD_SECONDS,
    UNDERLYING,
    boundary_bid_events,
    crossed_then_last_events,
    event,
    quote_events,
    single_bid_event,
    single_last_event,
    threshold_straddle_events,
)
from hypothesis import given, settings
from hypothesis import strategies as st

QC = QcThresholdConfig(
    version="qc-test",
    max_spread_pct=0.05,
    max_quote_age_seconds=STALE_THRESHOLD_SECONDS,
    min_chain_count=1,
)
CALC_TS = SNAPSHOT_TS + timedelta(seconds=5)


def context(**overrides: object) -> SnapshotContext:
    base: dict[str, object] = dict(
        snapshot_ts=SNAPSHOT_TS, qc=QC, calc_ts=CALC_TS, config_hashes={"cfg": "cfg-test"}
    )
    base.update(overrides)
    return SnapshotContext(**base)  # type: ignore[arg-type]


def test_latest_by_field_before_includes_exactly_at_excludes_after() -> None:
    latest = latest_by_field_before(boundary_bid_events(), SNAPSHOT_TS)
    assert latest["bid"].value == 190.5
    assert latest["bid"].canonical_ts == SNAPSHOT_TS


def test_no_future_event_leaks_even_when_only_future_exists() -> None:
    future_only = (event(UNDERLYING, "bid", 191.0, ts=SNAPSHOT_TS + timedelta(microseconds=1)),)
    assert latest_by_field_before(future_only, SNAPSHOT_TS) == {}


def test_latest_by_field_before_is_order_independent() -> None:
    events = boundary_bid_events()
    forward = latest_by_field_before(events, SNAPSHOT_TS)
    reversed_result = latest_by_field_before(tuple(reversed(events)), SNAPSHOT_TS)
    assert forward["bid"].event_id == reversed_result["bid"].event_id == "b-at"


_PROPERTY_TS = datetime(2026, 5, 29, 15, 30, 0, tzinfo=UTC)


def _reference_latest_by_field_before(
    events: Sequence[RawMarketEvent], snapshot_ts: datetime
) -> dict[str, RawMarketEvent]:
    latest: dict[str, RawMarketEvent] = {}
    for candidate in events:
        if candidate.canonical_ts > snapshot_ts:
            continue
        current = latest.get(candidate.field_name)
        supersedes = current is None or (
            candidate.canonical_ts > current.canonical_ts
            if candidate.canonical_ts != current.canonical_ts
            else candidate.event_id > current.event_id
        )
        if supersedes:
            latest[candidate.field_name] = candidate
    return latest


def _make_event(field_name: str, offset_seconds: int, event_id: str) -> RawMarketEvent:
    ts = _PROPERTY_TS + timedelta(seconds=offset_seconds)
    return event(UNDERLYING, field_name, float(offset_seconds), ts=ts, event_id=event_id)


_events_strategy = st.lists(
    st.builds(
        _make_event,
        field_name=st.sampled_from(["bid", "ask", "last"]),
        offset_seconds=st.integers(min_value=-3, max_value=3),
        event_id=st.text(alphabet="abcABC0123", min_size=1, max_size=4),
    ),
    min_size=0,
    max_size=12,
    unique_by=lambda e: e.event_id,
)


@settings(max_examples=400)
@given(events=_events_strategy)
def test_engine_as_of_matches_reference_loop(events: list[RawMarketEvent]) -> None:
    engine = latest_by_field_before(events, _PROPERTY_TS)
    reference = _reference_latest_by_field_before(events, _PROPERTY_TS)
    assert set(engine) == set(reference)
    for field_name in reference:
        assert engine[field_name].event_id == reference[field_name].event_id
        assert engine[field_name].canonical_ts == reference[field_name].canonical_ts
        assert engine[field_name].value == reference[field_name].value


@settings(max_examples=200)
@given(events=_events_strategy, seed=st.integers(min_value=0, max_value=10_000))
def test_engine_as_of_is_shuffle_invariant(
    events: list[RawMarketEvent], seed: int
) -> None:
    shuffled = list(events)
    random.Random(seed).shuffle(shuffled)
    forward = latest_by_field_before(events, _PROPERTY_TS)
    reshuffled = latest_by_field_before(shuffled, _PROPERTY_TS)
    assert set(forward) == set(reshuffled)
    for field_name in forward:
        assert forward[field_name].event_id == reshuffled[field_name].event_id


def test_engine_as_of_exact_tie_breaks_on_larger_event_id() -> None:
    early_id = event(UNDERLYING, "bid", 1.0, ts=_PROPERTY_TS, event_id="aaa")
    late_id = event(UNDERLYING, "bid", 2.0, ts=_PROPERTY_TS, event_id="zzz")
    assert latest_by_field_before((early_id, late_id), _PROPERTY_TS)["bid"].event_id == "zzz"
    assert latest_by_field_before((late_id, early_id), _PROPERTY_TS)["bid"].event_id == "zzz"


def test_mid_from_clean_two_sided_quote() -> None:
    ref = resolve_reference_spot(bid=190.4, ask=190.6, last=190.5)
    assert ref.value == pytest.approx(190.5)
    assert ref.reference_type == "mid"
    assert ref.is_fallback is False
    assert ref.spread_pct == pytest.approx(0.2 / 190.5)


@pytest.mark.parametrize(
    "kwargs, expected_type, expected_value",
    [
        (dict(bid=2.5, ask=2.0, last=2.2), "last", 2.2),
        (dict(bid=None, ask=None, last=3.0), "last", 3.0),
        (dict(bid=None, ask=None, last=None, prior_close=4.0), "close", 4.0),
        (dict(bid=None, ask=None, last=None, prior_spot=5.0), "carry_forward", 5.0),
    ],
)
def test_fallback_rungs_each_fire_and_are_labeled(
    kwargs: dict, expected_type: str, expected_value: float
) -> None:
    ref = resolve_reference_spot(**kwargs)
    assert ref.reference_type == expected_type
    assert ref.value == pytest.approx(expected_value)
    assert ref.is_fallback is True


def test_no_rung_available_raises() -> None:
    from algotrading.infra.snapshots import NoReferenceSpot

    with pytest.raises(NoReferenceSpot):
        resolve_reference_spot(bid=None, ask=None, last=None)


def test_clean_snapshot_has_mid_and_is_open_and_complete() -> None:
    events = quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5)
    snapshot = build_snapshot(UNDERLYING, events, context=context())
    assert snapshot.reference_type == "mid"
    assert snapshot.reference_spot == pytest.approx(190.5)
    assert snapshot.flags == ("open",)
    assert snapshot.completeness == pytest.approx(1.0)
    validate(snapshot)


def test_snapshot_is_order_independent() -> None:
    events = quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5)
    forward = build_snapshot(UNDERLYING, events, context=context())
    shuffled = build_snapshot(UNDERLYING, tuple(reversed(events)), context=context())
    assert forward == shuffled


def test_closed_session_flag() -> None:
    events = quote_events(UNDERLYING, bid=190.4, ask=190.6, ts=SNAPSHOT_TS)
    snapshot = build_snapshot(UNDERLYING, events, context=context(session_open=False))
    assert "closed" in snapshot.flags
    assert snapshot.completeness == pytest.approx(2.0 / 3.0)


def test_quote_exactly_at_threshold_is_not_stale() -> None:
    snapshot = build_snapshot(UNDERLYING, threshold_straddle_events(), context=context())
    assert "stale_underlying" not in snapshot.flags


def test_quote_just_over_threshold_is_stale() -> None:
    quote_ts = SNAPSHOT_TS - timedelta(seconds=STALE_THRESHOLD_SECONDS + 1.0)
    events = quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5, ts=quote_ts)
    snapshot = build_snapshot(UNDERLYING, events, context=context())
    assert "stale_underlying" in snapshot.flags


def test_stale_option_flag_uses_option_label() -> None:
    quote_ts = SNAPSHOT_TS - timedelta(seconds=STALE_THRESHOLD_SECONDS + 10.0)
    events = quote_events(OPTION, bid=2.9, ask=3.1, last=3.0, ts=quote_ts)
    snapshot = build_snapshot(OPTION, events, context=context())
    assert "stale_option" in snapshot.flags
    assert "stale_underlying" not in snapshot.flags


def test_option_inherits_stale_underlying_via_batch() -> None:
    stale_ts = SNAPSHOT_TS - timedelta(seconds=STALE_THRESHOLD_SECONDS + 10.0)
    events = (
        *quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5, ts=stale_ts),
        *quote_events(OPTION, bid=2.9, ask=3.1, last=3.0, ts=SNAPSHOT_TS),
    )
    batch = build_snapshots(
        [UNDERLYING, OPTION], events,
        snapshot_ts=SNAPSHOT_TS, qc=QC, calc_ts=CALC_TS, config_hashes={"cfg": "cfg-test"},
    )
    by_key = {snap.instrument_key: snap for snap in batch.snapshots}
    option_snapshot = by_key[OPTION.canonical()]
    assert "stale_underlying" in option_snapshot.flags
    assert "stale_option" not in option_snapshot.flags


def test_fallback_spot_flag_when_not_mid() -> None:
    snapshot = build_snapshot(UNDERLYING, single_last_event(), context=context())
    assert snapshot.reference_type == "last"
    assert "fallback_spot" in snapshot.flags


def test_empty_events_raises_insufficient() -> None:
    with pytest.raises(InsufficientSnapshotData):
        build_snapshot(UNDERLYING, (), context=context())


def test_single_bid_is_insufficient() -> None:
    with pytest.raises(InsufficientSnapshotData):
        build_snapshot(UNDERLYING, single_bid_event(), context=context())


def test_single_last_falls_back_to_last() -> None:
    snapshot = build_snapshot(UNDERLYING, single_last_event(), context=context())
    assert snapshot.reference_type == "last"
    assert snapshot.reference_spot == pytest.approx(190.5)


def test_all_stale_builds_snapshot_with_stale_flag() -> None:
    stale_ts = SNAPSHOT_TS - timedelta(seconds=STALE_THRESHOLD_SECONDS + 120.0)
    events = quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5, ts=stale_ts)
    snapshot = build_snapshot(UNDERLYING, events, context=context())
    assert snapshot.reference_type == "mid"
    assert "stale_underlying" in snapshot.flags


def test_crossed_quote_does_not_feed_mid() -> None:
    snapshot = build_snapshot(UNDERLYING, crossed_then_last_events(), context=context())
    assert snapshot.reference_type == "last"
    assert snapshot.reference_spot == pytest.approx(190.5)
    assert "fallback_spot" in snapshot.flags


def test_batch_collects_insufficient_as_labeled_skip() -> None:
    batch = build_snapshots(
        [UNDERLYING], single_bid_event(),
        snapshot_ts=SNAPSHOT_TS, qc=QC, calc_ts=CALC_TS, config_hashes={"cfg": "cfg-test"},
    )
    assert batch.snapshots == ()
    assert len(batch.skipped) == 1
    assert batch.skipped[0].instrument_key == UNDERLYING.canonical()


def test_batch_collects_an_option_with_no_spot_as_a_labeled_skip() -> None:
    events = (event(OPTION, "bid", 3.0, ts=SNAPSHOT_TS),)
    batch = build_snapshots(
        [OPTION], events,
        snapshot_ts=SNAPSHOT_TS, qc=QC, calc_ts=CALC_TS, config_hashes={"cfg": "cfg-test"},
    )
    assert batch.snapshots == ()
    assert [skip.instrument_key for skip in batch.skipped] == [OPTION.canonical()]


@pytest.mark.parametrize(
    "kwargs, expected_status, expected_reason",
    [
        (dict(bid=2.5, ask=2.0, max_spread_pct=0.05), "reject", "crossed"),
        (dict(bid=2.0, ask=2.0, max_spread_pct=0.05), "caution", "locked"),
        (dict(bid=None, ask=3.0, max_spread_pct=0.05), "caution", "non_positive_bid"),
        (dict(bid=1.0, ask=2.0, max_spread_pct=0.05), "caution", "wide_spread"),
        (
            dict(bid=1.9, ask=2.0, max_spread_pct=0.05, age_seconds=40.0,
                 max_quote_age_seconds=30.0),
            "caution", "stale",
        ),
        (
            dict(bid=1.9, ask=2.0, max_spread_pct=0.05, open_interest=5.0,
                 min_open_interest=100.0),
            "caution", "low_open_interest",
        ),
        (
            dict(bid=1.9, ask=2.0, max_spread_pct=0.05, price=1.0, intrinsic=2.0,
                 max_value=100.0),
            "reject", "below_intrinsic",
        ),
        (
            dict(bid=1.9, ask=2.0, max_spread_pct=0.05, price=200.0, intrinsic=0.0,
                 max_value=100.0),
            "reject", "above_max_value",
        ),
    ],
)
def test_named_quote_checks(kwargs: dict, expected_status: str, expected_reason: str) -> None:
    assessment = assess_quote(**kwargs)
    assert assessment.status == expected_status
    assert expected_reason in assessment.reasons


def test_clean_quote_is_usable_with_no_reasons() -> None:
    assessment = assess_quote(bid=1.99, ask=2.0, max_spread_pct=0.05)
    assert assessment.status == "usable"
    assert assessment.reasons == ()
    assert assessment.is_usable is True


def test_assess_quote_takes_worst_severity_and_keeps_all_reasons() -> None:
    assessment = assess_quote(
        bid=1.0, ask=2.0, max_spread_pct=0.05, price=0.1, intrinsic=1.0, max_value=100.0
    )
    assert assessment.status == "reject"
    assert "wide_spread" in assessment.reasons
    assert "below_intrinsic" in assessment.reasons


def test_cross_strike_monotonicity_flags_the_violation() -> None:
    strikes = (90.0, 95.0, 100.0, 105.0)
    monotone_calls = (12.0, 8.0, 5.0, 3.0)
    assert cross_strike_monotonicity_violations(strikes, monotone_calls) == ()
    broken_calls = (12.0, 8.0, 9.0, 3.0)
    assert cross_strike_monotonicity_violations(strikes, broken_calls) == (2,)


@pytest.mark.parametrize(
    "finding, expected",
    [
        (check_spread(3.0, 5.0, 0.6), None),
        (check_spread(3.0, 5.0, 0.5), None),
        (check_spread(3.0, 5.0, 0.4), ("caution", "wide_spread")),
        (check_quote_age(9.0, 10.0), None),
        (check_quote_age(10.0, 10.0), None),
        (check_quote_age(11.0, 10.0), ("caution", "stale")),
        (check_open_interest(101.0, 100.0), None),
        (check_open_interest(100.0, 100.0), None),
        (check_open_interest(99.0, 100.0), ("caution", "low_open_interest")),
        (check_price_against_intrinsic(5.0, 5.0, 100.0), None),
        (check_price_against_intrinsic(4.99, 5.0, 100.0), ("reject", "below_intrinsic")),
        (check_price_against_intrinsic(100.0, 5.0, 100.0), None),
        (check_price_against_intrinsic(100.01, 5.0, 100.0), ("reject", "above_max_value")),
        (check_crossed_or_locked(0.99, 1.0), None),
        (check_crossed_or_locked(1.0, 1.0), ("caution", "locked")),
        (check_crossed_or_locked(1.01, 1.0), ("reject", "crossed")),
        (check_bid_positive(0.01), None),
        (check_bid_positive(0.0), ("caution", "non_positive_bid")),
        (check_bid_positive(-1.0), ("caution", "non_positive_bid")),
    ],
)
def test_quote_quality_predicate_boundaries(
    finding: tuple[str, str] | None, expected: tuple[str, str] | None
) -> None:
    assert finding == expected


def test_snapshot_round_trips_as_a_valid_contract() -> None:
    events = quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5)
    snapshot = build_snapshot(UNDERLYING, events, context=context())
    assert isinstance(snapshot, MarketStateSnapshot)
    validate(snapshot)
    assert len(snapshot.provenance.source_records) == 3
    assert all(ref.table == "raw_market_events" for ref in snapshot.provenance.source_records)


def test_assess_snapshot_pairs_the_same_snapshot_with_a_verdict() -> None:
    events = quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5)
    assessed = assess_snapshot(UNDERLYING, events, context=context())
    assert assessed.snapshot == build_snapshot(UNDERLYING, events, context=context())
    assert assessed.assessment.status == "usable"
    assert assessed.assessment.reasons == ()
    assert assessed.assessment.is_usable is True


def test_crossed_quote_is_rejected_by_qc_but_snapshot_still_built() -> None:
    assessed = assess_snapshot(UNDERLYING, crossed_then_last_events(), context=context())
    assert assessed.snapshot.reference_type == "last"
    assert "fallback_spot" in assessed.snapshot.flags
    assert assessed.assessment.status == "reject"
    assert "crossed" in assessed.assessment.reasons
    assert assessed.assessment.is_usable is False


def test_wide_spread_quote_is_caution_and_still_usable() -> None:
    events = quote_events(UNDERLYING, bid=180.0, ask=200.0, last=190.0)
    assessed = assess_snapshot(UNDERLYING, events, context=context())
    assert assessed.snapshot.reference_type == "mid"
    assert assessed.assessment.status == "caution"
    assert "wide_spread" in assessed.assessment.reasons
    assert assessed.assessment.is_usable is True


def test_fallback_only_snapshot_is_caution_not_reject() -> None:
    assessed = assess_snapshot(UNDERLYING, single_last_event(), context=context())
    assert assessed.snapshot.reference_type == "last"
    assert assessed.assessment.status == "caution"
    assert "non_positive_bid" in assessed.assessment.reasons
    assert assessed.assessment.is_usable is True


def test_qc_stale_verdict_agrees_with_the_stale_flag() -> None:
    quote_ts = SNAPSHOT_TS - timedelta(seconds=STALE_THRESHOLD_SECONDS + 1.0)
    events = quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5, ts=quote_ts)
    assessed = assess_snapshot(UNDERLYING, events, context=context())
    assert "stale_underlying" in assessed.snapshot.flags
    assert assessed.assessment.status == "caution"
    assert "stale" in assessed.assessment.reasons
    assert assessed.assessment.is_usable is True


def test_batch_keeps_full_set_and_qc_filtered_usable_view() -> None:
    events = (
        *quote_events(UNDERLYING, bid=190.4, ask=190.6, last=190.5),
        *quote_events(OPTION, bid=3.1, ask=2.9, last=3.0),
    )
    batch = build_snapshots(
        [UNDERLYING, OPTION], events,
        snapshot_ts=SNAPSHOT_TS, qc=QC, calc_ts=CALC_TS, config_hashes={"cfg": "cfg-test"},
    )
    assert len(batch.snapshots) == 2
    assert len(batch.assessed) == 2
    assert {snap.instrument_key for snap in batch.usable} == {UNDERLYING.canonical()}
    by_key = {item.snapshot.instrument_key: item for item in batch.assessed}
    rejected = by_key[OPTION.canonical()]
    assert rejected.assessment.status == "reject"
    assert "crossed" in rejected.assessment.reasons
    assert OPTION.canonical() in {snap.instrument_key for snap in batch.snapshots}
