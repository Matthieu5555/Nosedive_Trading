"""IBKR historical-bar backfill collector (ADR 0031, Part C).

No live Gateway: a fake transport returns canned history payloads (and can raise to exercise
retry/backoff). The store is a real ``ParquetStore`` over ``tmp_path`` (the seam is the actual
write/read, per the never-smoke-test-against-canonical-data rule). The named obligations from
the 1C spec's test surface:

* read-only invariant — the path touches only ``/iserver/marketdata/*``, never an order endpoint;
* session gating — a fetch before the session is established is raised, not sent;
* retry/backoff — a transient transport failure is retried with the injected (no-real-sleep)
  backoff, and exhausting attempts raises a labeled error;
* backfill resume — a run killed after K of N tickers re-fetches only the missing tail, and the
  final on-disk set equals an uninterrupted run;
* edge cases — empty basket, a ticker with no history in the window.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from algotrading.core.provenance import source_ref
from algotrading.infra.storage import ParquetStore
from algotrading.infra_ibkr.collectors.cp_rest_history import (
    CpRestHistoryCollector,
    HistoryFetchError,
    HistoryRequest,
)
from algotrading.infra_ibkr.config import load_ibkr_history_config
from algotrading.infra_ibkr.connectivity.cp_rest_transport import CpRestTransportError
from fixtures.records import make_stamp

from .conftest import FakeCpTransport

_T0_MS = int((datetime(2026, 6, 4, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000)
_T1_MS = int((datetime(2026, 6, 5, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000)


def _payload(underlying: str) -> dict[str, Any]:
    return {
        "symbol": underlying,
        "data": [
            {"t": _T0_MS, "o": 99.0, "h": 101.5, "l": 98.5, "c": 100.25, "v": 1_000_000},
            {"t": _T1_MS, "o": 100.25, "h": 102.0, "l": 99.75, "c": 101.5, "v": 2_000_000},
        ],
    }


def _history_transport(
    payloads: dict[int, dict[str, Any]],
    *,
    errors: list[Exception] | None = None,
    conid_errors: dict[int, Exception] | None = None,
) -> FakeCpTransport:
    """History GETs answer a per-conid payload; POSTs are no-ops.

    ``errors`` is a FIFO raised ahead of the next GETs (transient-retry tests);
    ``conid_errors`` makes one conid permanently unservable (boundary-failure tests).
    """

    def _by_conid(_path: str, params: dict[str, Any]) -> dict[str, Any]:
        conid = int(params.get("conid", 0))
        if conid_errors and conid in conid_errors:
            raise conid_errors[conid]
        return payloads.get(conid, {"data": []})

    return FakeCpTransport(get_responder=_by_conid, get_errors=errors, post_response=None)


def _provenance_for(underlying: str, trade_date: date) -> object:
    return make_stamp(
        (source_ref("raw_market_events", "ibkr-history", f"{underlying}-{trade_date}"),)
    )


def _collector(
    transport: FakeCpTransport,
    store: ParquetStore,
    *,
    established: bool = True,
) -> CpRestHistoryCollector:
    return CpRestHistoryCollector(
        transport=transport,
        store=store,
        config=load_ibkr_history_config(),
        provider="IBKR",
        is_established=lambda: established,
        provenance_for=_provenance_for,
        sleep=lambda _seconds: None,  # injected: no real waiting in tests
    )


# -- happy path: fetch + persist round-trips to DailyBar ----------------------------------
def test_fetch_normalizes_and_backfill_persists_bars(tmp_path: Path) -> None:
    transport = _history_transport({8314: _payload("AAPL")})
    store = ParquetStore(tmp_path)
    collector = _collector(transport, store)
    result = collector.backfill([HistoryRequest("AAPL", 8314, "1y")])
    assert result.fetched == ("AAPL",)
    assert result.bar_count == 2
    bars = store.read("daily_bar", provider="IBKR")
    assert sorted(b.trade_date for b in bars) == [date(2026, 6, 4), date(2026, 6, 5)]
    assert {b.close for b in bars} == {100.25, 101.5}


# -- read-only invariant (mirror test_cp_rest_adapter) -----------------------------------
def test_history_path_is_read_only(tmp_path: Path) -> None:
    transport = _history_transport({8314: _payload("AAPL")})
    _collector(transport, ParquetStore(tmp_path)).backfill([HistoryRequest("AAPL", 8314, "1y")])
    # Only the market-data history endpoint is touched (warmup + the real fetch); never an order.
    assert set(transport.get_paths) == {"/iserver/marketdata/history"}
    assert transport.post_paths == []
    assert not any("order" in p for p in transport.get_paths + transport.post_paths)


# -- warmup is a throwaway: a failing warmup must not abort the real fetch -----------------
def test_warmup_failure_is_non_fatal(tmp_path: Path) -> None:
    """A 503 (or any error) on the throwaway warmup probe is swallowed; the real fetch proceeds.

    The warmup wakes IBKR's data farm and its own result is never used — over the local CP
    Gateway the ``conid=0`` probe answers 503. The queued error is popped by the warmup's first
    ``get``; the subsequent real fetch (conid 8314) still returns its payload and persists.
    """
    transport = _history_transport({8314: _payload("AAPL")}, errors=[RuntimeError("503 on warmup")])
    result = _collector(transport, ParquetStore(tmp_path)).backfill(
        [HistoryRequest("AAPL", 8314, "1y")]
    )
    assert result.fetched == ("AAPL",) and result.bar_count == 2


# -- session gating ----------------------------------------------------------------------
def test_fetch_before_established_is_raised_not_sent(tmp_path: Path) -> None:
    transport = _history_transport({8314: _payload("AAPL")})
    collector = _collector(transport, ParquetStore(tmp_path), established=False)
    with pytest.raises(HistoryFetchError, match="not established"):
        collector.fetch(HistoryRequest("AAPL", 8314, "1y"))
    # Nothing went to the wire — the request was refused, not sent into a dead session.
    assert transport.get_paths == []


# -- retry/backoff -----------------------------------------------------------------------
def test_transient_failure_is_retried_then_succeeds(tmp_path: Path) -> None:
    # The first fetch GET raises (maintenance window); the retry succeeds. The slept delays are
    # recorded via a capturing sleep so the backoff schedule is asserted, not just the outcome.
    slept: list[float] = []
    transport = _history_transport({8314: _payload("AAPL")}, errors=[RuntimeError("503 maintenance")])
    collector = CpRestHistoryCollector(
        transport=transport,
        store=ParquetStore(tmp_path),
        config=load_ibkr_history_config(),
        provider="IBKR",
        is_established=lambda: True,
        provenance_for=_provenance_for,
        sleep=slept.append,
    )
    collector._warmed_up = True  # skip warmup so the queued error lands on the fetch retry path
    bars = collector.fetch(HistoryRequest("AAPL", 8314, "1y"))
    assert len(bars) == 2
    assert slept == [2.0]  # one backoff delay (base_seconds) before the successful retry


def test_exhausted_retries_raise_labeled_error(tmp_path: Path) -> None:
    errors = [RuntimeError("down")] * 5  # max_attempts in the config
    transport = _history_transport({8314: _payload("AAPL")}, errors=errors)
    collector = _collector(transport, ParquetStore(tmp_path))
    collector._warmed_up = True
    with pytest.raises(HistoryFetchError, match="failed after"):
        collector.fetch(HistoryRequest("AAPL", 8314, "1y"))


# -- backfill resume ---------------------------------------------------------------------
def test_backfill_resume_refetches_only_the_missing_tail(tmp_path: Path) -> None:
    payloads = {8314: _payload("AAPL"), 4567: _payload("MSFT"), 9999: _payload("GOOG")}
    requests = [
        HistoryRequest("AAPL", 8314, "1y"),
        HistoryRequest("MSFT", 4567, "1y"),
        HistoryRequest("GOOG", 9999, "1y"),
    ]
    # First run "killed" after the first ticker: only AAPL on disk.
    store = ParquetStore(tmp_path)
    partial = _collector(_history_transport(payloads), store)
    partial.backfill(requests[:1])

    # Restart over the full list: AAPL is skipped (already on disk), MSFT+GOOG fetched.
    resume_transport = _history_transport(payloads)
    resumed = _collector(resume_transport, store)
    result = resumed.backfill(requests)
    assert result.skipped == ("AAPL",)
    assert sorted(result.fetched) == ["GOOG", "MSFT"]
    # The history endpoint was hit only for the two missing tickers (plus their warmup share).
    assert resume_transport.get_paths.count("/iserver/marketdata/history") >= 2

    # The final on-disk set equals an uninterrupted run from scratch.
    fresh = ParquetStore(tmp_path / "fresh")
    _collector(_history_transport(payloads), fresh).backfill(requests)
    key = lambda b: (b.underlying, b.trade_date)  # noqa: E731
    assert sorted(store.read("daily_bar"), key=key) == sorted(fresh.read("daily_bar"), key=key)


def test_empty_basket_writes_nothing(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    result = _collector(_history_transport({}), store).backfill([])
    assert result.fetched == () and result.bar_count == 0
    assert store.read("daily_bar") == []


def test_ticker_with_no_history_in_window_persists_no_bars(tmp_path: Path) -> None:
    # The conid returns an empty data window; the ticker is "fetched" but writes zero bars.
    transport = _history_transport({8314: {"symbol": "AAPL", "data": []}})
    store = ParquetStore(tmp_path)
    result = _collector(transport, store).backfill([HistoryRequest("AAPL", 8314, "1y")])
    assert result.fetched == ("AAPL",)
    assert result.bar_count == 0
    assert store.read("daily_bar") == []


# -- pagination: page backward over the ~999-bar/request cap to the full history -----------
def _epoch_ms(d: date) -> int:
    return int((datetime(d.year, d.month, d.day, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC))
               .total_seconds() * 1000)


def _series(n: int) -> list[dict[str, Any]]:
    """``n`` consecutive synthetic daily bars (strictly valid OHLC, ascending by date)."""
    base = date(2016, 1, 4).toordinal()
    out: list[dict[str, Any]] = []
    for i in range(n):
        d = date.fromordinal(base + i)
        out.append({"date": d, "t": _epoch_ms(d),
                    "o": 100.0 + i, "h": 101.0 + i, "l": 99.0 + i, "c": 100.5 + i, "v": 1000 + i})
    return out


def _terminal_transport_error(code: int) -> CpRestTransportError:
    """A transport error carrying its HTTP status — the real ``CpRestTransport`` shape (M20:
    ``status_code`` is a first-class field; the old ``__cause__.response`` reach is gone)."""
    return CpRestTransportError(f"GET history failed: HTTP {code}", status_code=code)


class _PaginatedGateway:
    """Fake CP history endpoint: a long series, returns <=cap bars ENDING at ``startTime``.

    Models the verified live semantics — ``startTime`` is the END anchor and a request returns at
    most ``cap`` bars going back from it — and, like the real CP Gateway, raises an HTTP **500**
    when the window is at/before the start of available history (the boundary the pager must
    tolerate), rather than a clean empty response.
    """

    def __init__(self, series: list[dict[str, Any]], *, cap: int = 999) -> None:
        self._series = series
        self._cap = cap
        self.window_starts: list[str | None] = []  # the startTime of each REAL (conid!=0) window

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        p = params or {}
        if str(p.get("conid")) == "0":  # the throwaway warmup probe
            return {"data": []}
        start_time = p.get("startTime")
        self.window_starts.append(start_time)
        if start_time is None:
            end_idx = len(self._series) - 1  # the most-recent window
        else:
            end = date(int(start_time[0:4]), int(start_time[4:6]), int(start_time[6:8]))
            if end <= self._series[0]["date"]:
                raise _terminal_transport_error(500)  # before the start of history — IBKR 500s
            end_idx = max(i for i, b in enumerate(self._series) if b["date"] <= end)
        start_idx = max(0, end_idx - self._cap + 1)
        window = self._series[start_idx : end_idx + 1]
        return {"data": [{k: b[k] for k in ("t", "o", "h", "l", "c", "v")} for b in window]}

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return None


def test_fetch_pages_backward_over_the_cap_to_full_history(tmp_path: Path) -> None:
    """A 2300-day history (>2x the 999 cap) is reassembled in full by paging ``startTime`` back.

    Every distinct day is merged exactly once, in ascending order, with no gap or duplicate. The
    oldest window raises the boundary HTTP 500 (as the live Gateway does) and the pager stops
    cleanly on it — returning the full paged history rather than crashing.
    """
    series = _series(2300)
    gateway = _PaginatedGateway(series, cap=999)
    bars = _collector(gateway, ParquetStore(tmp_path)).fetch(HistoryRequest("AAPL", 8314, "5y"))

    assert [b.trade_date for b in bars] == [s["date"] for s in series]  # full, ordered, deduped
    assert len(bars) == 2300
    assert len(gateway.window_starts) >= 3  # the cap forced multiple windows
    assert gateway.window_starts[0] is None  # first window is the most-recent (unanchored)
    assert all(start for start in gateway.window_starts[1:])  # later windows anchor backward


def test_first_window_failure_is_surfaced_not_swallowed(tmp_path: Path) -> None:
    """A boundary error on the FIRST (most-recent) window is a genuine failure, raised — not empty."""
    transport = _history_transport({8314: _payload("AAPL")}, errors=[_terminal_transport_error(500)])
    collector = _collector(transport, ParquetStore(tmp_path))
    collector._warmed_up = True  # the queued 500 lands on the first real window
    with pytest.raises(HistoryFetchError, match="HTTP 500"):
        collector.fetch(HistoryRequest("AAPL", 8314, "5y"))


def test_terminal_status_is_not_retried(tmp_path: Path) -> None:
    """A 404/500 window fails fast (no backoff burned); only 503/timeouts are retried."""
    slept: list[float] = []
    transport = _history_transport({8314: _payload("AAPL")}, errors=[_terminal_transport_error(404)])
    collector = CpRestHistoryCollector(
        transport=transport, store=ParquetStore(tmp_path), config=load_ibkr_history_config(),
        provider="IBKR", is_established=lambda: True, provenance_for=_provenance_for,
        sleep=slept.append,
    )
    collector._warmed_up = True
    with pytest.raises(HistoryFetchError, match="HTTP 404"):
        collector.fetch(HistoryRequest("AAPL", 8314, "5y"))
    assert slept == []  # not a single backoff — the terminal status short-circuited the retry


def test_window_cap_bounds_a_nonterminating_feed(tmp_path: Path) -> None:
    """The safety cap stops a feed that never runs dry, returning what was paged (never hangs)."""
    series = _series(5000)
    gateway = _PaginatedGateway(series, cap=999)
    collector = _collector(gateway, ParquetStore(tmp_path))
    collector.max_history_windows = 2  # force the backstop before the series is exhausted
    bars = collector.fetch(HistoryRequest("AAPL", 8314, "5y"))
    # Two windows of fresh data only (~1998 bars), bounded — not the full 5000, and no infinite loop.
    assert 0 < len(bars) <= 999 * 2
    assert len(gateway.window_starts) == 2


def test_backfill_continues_past_a_failed_ticker(tmp_path: Path) -> None:
    """A ticker whose fetch fails is recorded in ``failed`` and the sweep finishes the rest.

    AAPL/GOOG resolve and persist; BADX (conid 7) always answers a boundary 500 (a constituent
    IBKR cannot serve) — it lands in ``failed`` without aborting the other two.
    """
    routed = _history_transport(
        {8314: _payload("AAPL"), 9999: _payload("GOOG")},
        conid_errors={7: _terminal_transport_error(500)},  # BADX — always a boundary error
    )
    result = _collector(routed, ParquetStore(tmp_path)).backfill(
        [HistoryRequest("AAPL", 8314, "1y"),
         HistoryRequest("BADX", 7, "1y"),
         HistoryRequest("GOOG", 9999, "1y")]
    )
    assert sorted(result.fetched) == ["AAPL", "GOOG"]
    assert result.failed == ("BADX",)
    assert result.bar_count == 4  # AAPL (2) + GOOG (2); BADX contributed none
    assert {b.underlying for b in ParquetStore(tmp_path).read("daily_bar")} == {"AAPL", "GOOG"}


def test_backfill_presence_scan_is_one_pass_not_a_read_per_ticker(tmp_path: Path) -> None:
    """The skip-if-present check costs ONE partition-name scan for the whole sweep.

    The old shape read the entire daily_bar table back into contracts once PER TICKER —
    O(tickers × files), the real stall behind the observed ~3 names/10 min on the live
    store (419k files). The presence set must come from one `underlyings_present` call,
    and `read` must not be used for presence at all.
    """
    class _CountingStore(ParquetStore):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.read_calls = 0
            self.presence_calls = 0

        def read(self, table: str, **kwargs: Any) -> list[Any]:
            self.read_calls += 1
            return super().read(table, **kwargs)

        def underlyings_present(self, table: str, *, provider: str | None = None) -> frozenset[str]:
            self.presence_calls += 1
            return super().underlyings_present(table, provider=provider)

    store = _CountingStore(tmp_path)
    # NVDA is already on disk; AAPL and GOOG are not.
    seeded = _collector(_history_transport({4815: _payload("NVDA")}), store)
    seeded.backfill([HistoryRequest("NVDA", 4815, "1y")])
    store.read_calls = 0
    store.presence_calls = 0

    transport = _history_transport({8314: _payload("AAPL"), 9999: _payload("GOOG")})
    result = _collector(transport, store).backfill(
        [HistoryRequest("NVDA", 4815, "1y"),
         HistoryRequest("AAPL", 8314, "1y"),
         HistoryRequest("GOOG", 9999, "1y")]
    )
    assert result.skipped == ("NVDA",)
    assert sorted(result.fetched) == ["AAPL", "GOOG"]
    assert store.presence_calls == 1  # one scan for the whole sweep
    assert store.read_calls == 0  # presence never goes through a full-table read


# -- refresh-tail: roll an already-present ticker forward to a new session -----------------
def test_refresh_tail_rolls_a_present_ticker_forward_in_one_window(tmp_path: Path) -> None:
    """A present ticker under ``refresh_tail`` re-fetches ONLY its most-recent window.

    The underlying-level presence scan otherwise freezes a seeded ticker at the day it was first
    backfilled (it is skipped wholesale, never advancing to a new session). ``refresh_tail`` rolls
    it forward by fetching a single unanchored window (today's ~999 bars) — NOT re-paging years of
    history — and the new session's bar lands via the idempotent ``(provider, underlying, date)``
    write. It lands in ``refreshed``, not ``skipped``.
    """
    store = ParquetStore(tmp_path)
    # Day 1: seed a long history so the ticker is "present" on disk.
    _collector(_PaginatedGateway(_series(1500), cap=999), store).backfill(
        [HistoryRequest("AAPL", 8314, "5y")]
    )
    assert "AAPL" in store.underlyings_present("daily_bar", provider="IBKR")

    # Day 2: one new session has appeared at the end of the series.
    series_day2 = _series(1501)
    new_session = series_day2[-1]["date"]
    assert new_session not in {b.trade_date for b in store.read("daily_bar", provider="IBKR")}

    roll_gw = _PaginatedGateway(series_day2, cap=999)
    result = _collector(roll_gw, store).backfill(
        [HistoryRequest("AAPL", 8314, "5y")], refresh_tail=True
    )
    assert result.refreshed == ("AAPL",)
    assert result.skipped == () and result.fetched == ()
    # Exactly one real window, unanchored (most-recent) — no backward paging for a roll-forward.
    assert roll_gw.window_starts == [None]
    assert new_session in {b.trade_date for b in store.read("daily_bar", provider="IBKR")}


def test_refresh_tail_off_still_skips_a_present_ticker(tmp_path: Path) -> None:
    """Default (``refresh_tail`` off) is unchanged: a present ticker is skipped, not re-fetched."""
    store = ParquetStore(tmp_path)
    _collector(_history_transport({8314: _payload("AAPL")}), store).backfill(
        [HistoryRequest("AAPL", 8314, "1y")]
    )
    roll_gw = _PaginatedGateway(_series(1501), cap=999)
    result = _collector(roll_gw, store).backfill([HistoryRequest("AAPL", 8314, "5y")])
    assert result.skipped == ("AAPL",) and result.refreshed == ()
    assert roll_gw.window_starts == []  # never hit the wire for the skipped ticker


def test_refresh_tail_still_full_fetches_an_absent_ticker(tmp_path: Path) -> None:
    """``refresh_tail`` only changes present tickers; an absent one still gets full back-paging."""
    gw = _PaginatedGateway(_series(2300), cap=999)
    result = _collector(gw, ParquetStore(tmp_path)).backfill(
        [HistoryRequest("AAPL", 8314, "5y")], refresh_tail=True
    )
    assert result.fetched == ("AAPL",) and result.refreshed == ()
    assert len(gw.window_starts) >= 3  # full backward paging, not a single window
