"""IBKR historical daily-OHLC backfill collector (ADR 0031).

The history twin of :mod:`.cp_rest_adapter` (live snapshot/WS): it pulls daily OHLC bars over
``GET /iserver/marketdata/history`` (``bar=1d``), normalizes them to :class:`DailyBar` rows
(:mod:`.cp_rest_history_normalize`), and writes them to the immutable, provider-partitioned
``daily_bar`` table through A's ``ParquetStore`` (write-ahead validated). It is built for
unattended operation per ADR 0031 §5:

* **Read-only invariant.** It touches only ``/iserver/marketdata/*`` (and the warmup is the
  same endpoint) — never an order endpoint. The ADR 0024 §4 invariant, extended to history.
* **Established-session gate.** A fetch is refused unless the brokerage session reports
  ``established: true`` (the caller runs :meth:`CpRestSession.wait_until_established` first);
  the collector double-checks via the injected ``is_established`` predicate and defers/raises
  rather than firing a request into a dead session.
* **Warmup + concurrency cap.** Honours IBKR's history "warmup" call and the
  5-concurrent-request cap (the cap is respected by issuing one ticker at a time here; the
  config carries the number for a future parallel driver).
* **Retry/backoff.** Wraps each fetch in the config's exponential-with-cap retry, for the
  unavoidable overlap with IBKR maintenance windows (the scheduler also runs off-window).
* **Resumable / idempotent.** :meth:`backfill` skips any ticker whose bar set is already on
  disk for the requested window, so a backfill killed after K of N tickers re-fetches only
  the missing tail; the on-disk set is identical to an uninterrupted run (idempotent on
  ``(provider, underlying, trade_date)`` — storage replaces, never duplicates).

No clock or sleep is read directly: the warmup/retry sleep is the injected ``sleep`` callable,
so tests drive the whole path with no real waiting. Secrets never appear here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

import structlog
from algotrading.infra.contracts import DailyBar
from algotrading.infra.storage import ParquetStore

from ..config import IbkrHistoryConfig
from .cp_rest_history_normalize import history_to_daily_bars

_LOGGER = structlog.get_logger("ibkr.history")

_HISTORY_PATH = "/iserver/marketdata/history"

# HTTP statuses that mean "no data for THIS window", not a transient outage — so the backward
# pager must NOT burn its retry budget on them. Observed live over the CP Gateway: 503 = a
# maintenance/warmup blip (retry); 404/500 = a window before the ticker's first listed bar (the
# start of available history). A 404/500 stops the paging for that ticker cleanly.
_TERMINAL_WINDOW_STATUSES = frozenset({404, 500})


def _window_http_status(exc: BaseException) -> int | None:
    """The HTTP status behind a transport error, if any (read from the wrapped cause).

    ``CpRestTransport`` raises its error ``from`` the underlying ``httpx`` status error, so the
    code is at ``exc.__cause__.response.status_code``. Duck-typed (no httpx import) and ``None``
    when the failure carried no HTTP response (a timeout, a connect error, a test stub).
    """
    response = getattr(getattr(exc, "__cause__", None), "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


class HistoryFetchError(Exception):
    """A history fetch failed after exhausting retries, or a precondition was unmet — labeled."""


class _SupportsGet(Protocol):
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


@dataclass(frozen=True, slots=True)
class HistoryRequest:
    """One ticker's history request: the underlying, its IBKR conid, and the window."""

    underlying: str
    conid: int
    period: str


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """What a backfill run did: tickers fetched, skipped (already on disk), or failed.

    ``failed`` is the resilience seam: in a large constituent sweep a ticker that does not resolve
    or is not entitled must not abort the rest — its labeled error is logged and it lands here, so
    the run completes and the operator sees exactly which tickers need attention.
    """

    fetched: tuple[str, ...]
    skipped: tuple[str, ...]
    bar_count: int
    failed: tuple[str, ...] = ()

    @property
    def attempted(self) -> tuple[str, ...]:
        return tuple(sorted({*self.fetched, *self.skipped, *self.failed}))


@dataclass
class CpRestHistoryCollector:
    """Fetch + normalize + persist IBKR daily OHLC, unattended-hardened (ADR 0031).

    ``transport`` is anything with a ``get`` (the OAuth-signed :class:`CpRestTransport`, or a
    fake). ``store`` is the destination ``ParquetStore``. ``config`` carries the no-hardcode
    connectivity knobs (warmup, cap, retry). ``is_established`` is the established-session
    predicate; ``provenance_for`` builds a per-bar stamp; ``sleep`` is the injected backoff
    sleep (no real wait in tests).
    """

    transport: _SupportsGet
    store: ParquetStore
    config: IbkrHistoryConfig
    provider: str
    is_established: Callable[[], bool]
    provenance_for: Callable[[str, date], object]
    sleep: Callable[[float], None]
    bar_type: str = "1d-TRADES"
    source: str = "cp-rest-history"
    # Safety backstop on the backward-paging loop (CP REST caps a request at ~999 daily bars, so
    # full history needs many windows). 40 windows × ~999 trading days ≈ 160 years — far beyond any
    # listed equity/index, so a real ticker stops on its own (an empty/duplicate window) long before
    # this. It only bounds a pathological non-terminating feed; if hit, it is logged, never silent.
    max_history_windows: int = 40
    _warmed_up: bool = field(default=False, init=False)

    def warmup(self) -> None:
        """Issue IBKR's history "warmup" call once (ADR 0031 §5).

        IBKR requires a throwaway history request to warm the data farm before real requests
        return reliably. Idempotent: only the first call hits the wire. Read-only — it is the
        same ``/iserver/marketdata/history`` endpoint, never an order path.
        """
        if self._warmed_up or not self.config.warmup_required:
            self._warmed_up = True
            return
        # The warmup is a THROWAWAY to wake IBKR's history data farm — its own result is never
        # used, so a failure here (e.g. the local CP Gateway answers the `conid=0` probe with a
        # 503) must NOT abort the real fetches that follow. Swallow it, logged, and proceed; a
        # genuinely cold farm surfaces as a retried failure on the first real `fetch`, not here.
        warmup_params = {"conid": "0", "period": "1d", "bar": self.config.bar}
        try:
            self.transport.get(_HISTORY_PATH, warmup_params)
        except Exception as exc:  # noqa: BLE001 — a throwaway probe; any failure is non-fatal
            _LOGGER.info(
                "ibkr.history.warmup_skipped",
                reason="throwaway warmup call failed; proceeding to real fetches",
                error=str(exc),
            )
        self._warmed_up = True

    def fetch(self, request: HistoryRequest) -> tuple[DailyBar, ...]:
        """Fetch a ticker's full available daily history, paging back over the ~999-bar cap.

        Refuses to fire unless the brokerage session is established (defers with a labeled
        :class:`HistoryFetchError` otherwise — never a request into a dead session). CP REST caps
        a single ``/iserver/marketdata/history`` request at ~999 daily bars (~4y), so this pages
        **backward**: ``startTime`` is the END anchor (verified live — a request returns ``period``
        of bars *ending* at ``startTime``), so each window re-anchors at the running earliest date
        and reaches ~999 bars further back, until a window returns no new day (the start of the
        ticker's listed history) or the safety :attr:`max_history_windows` cap. Each window's GET
        is wrapped in the config's exponential-with-cap retry. Returns the merged, date-sorted
        :class:`DailyBar` tuple (empty for a ticker with no history).
        """
        if not self.is_established():
            raise HistoryFetchError(
                f"brokerage session not established; deferring history fetch for "
                f"{request.underlying!r}"
            )
        self.warmup()
        return self._fetch_all_windows(request)

    def _fetch_all_windows(self, request: HistoryRequest) -> tuple[DailyBar, ...]:
        """Page backward window by window, deduping by ``trade_date`` (see :meth:`fetch`)."""
        by_date: dict[date, DailyBar] = {}
        start_time: str | None = None  # first window: the most recent ~999 bars (no anchor)
        for _window in range(self.max_history_windows):
            try:
                payload = self._get_with_retry(request, start_time=start_time)
            except HistoryFetchError:
                # A window failed. If it is the FIRST (most-recent) window we have nothing — that is
                # a genuine failure, surfaced. Deeper in, it is the start of available history (the
                # boundary 404/500) or a transient gap: stop paging and keep the history gathered so
                # far (resumable). Never crash a multi-year backfill on the oldest window.
                if not by_date:
                    raise
                _LOGGER.info(
                    "ibkr.history.paging_stopped",
                    underlying=request.underlying,
                    earliest=min(by_date).isoformat(),
                    reason="oldest window unavailable (start of history or transient); "
                    "returning the history paged so far",
                )
                break
            bars = history_to_daily_bars(
                payload,
                provider=self.provider,
                underlying=request.underlying,
                bar_type=self.bar_type,
                source=self.source,
                provenance_for=lambda d: self.provenance_for(request.underlying, d),
            )
            if not any(bar.trade_date not in by_date for bar in bars):
                break  # only already-seen days (or none): the start of available history
            by_date.update({bar.trade_date: bar for bar in bars})
            # END-anchor the next window at the current earliest; the 1-day overlap is deduped.
            start_time = min(by_date).strftime("%Y%m%d-00:00:00")
        else:
            _LOGGER.info(
                "ibkr.history.window_cap_reached",
                underlying=request.underlying,
                windows=self.max_history_windows,
                earliest=min(by_date).isoformat() if by_date else None,
                reason="hit the safety window cap; older history (if any) was not fetched",
            )
        return tuple(sorted(by_date.values(), key=lambda bar: bar.trade_date))

    def _get_with_retry(
        self, request: HistoryRequest, *, start_time: str | None = None
    ) -> dict[str, Any]:
        """GET the history endpoint with exponential-with-cap retry around transient failures.

        ``start_time`` (IBKR ``startTime``, format ``YYYYMMDD-HH:mm:ss``) is the END anchor of the
        window; ``None`` is the most-recent window. Each window pages ~999 bars back from it.
        """
        params = {"conid": str(request.conid), "period": request.period, "bar": self.config.bar}
        if start_time is not None:
            params["startTime"] = start_time
        last_error: Exception | None = None
        for attempt in range(self.config.retry.max_attempts):
            try:
                payload = self.transport.get(_HISTORY_PATH, params)
            except Exception as exc:  # transport-level failure (maintenance window, timeout)
                last_error = exc
                # A 404/500 is a definitive "no data for this window" (the start of history), not a
                # transient outage — fail fast so the backward pager does not burn its retry budget
                # (and minutes of backoff) on the oldest window of every ticker.
                status = _window_http_status(exc)
                if status in _TERMINAL_WINDOW_STATUSES:
                    raise HistoryFetchError(
                        f"history window for {request.underlying!r} returned HTTP {status} "
                        f"(no data for the requested window)"
                    ) from exc
                if attempt + 1 < self.config.retry.max_attempts:
                    self.sleep(self.config.retry.delay_for(attempt))
                continue
            if not isinstance(payload, dict):
                raise HistoryFetchError(
                    f"history payload for {request.underlying!r} is not a mapping: {payload!r}"
                )
            return payload
        raise HistoryFetchError(
            f"history fetch for {request.underlying!r} failed after "
            f"{self.config.retry.max_attempts} attempts: {last_error}"
        )

    def backfill(
        self, requests: Sequence[HistoryRequest], *, correlation_id: str = ""
    ) -> BackfillResult:
        """Backfill every requested ticker, skipping those already on disk (resumable).

        Iterates the requests (one ticker at a time, honouring the 5-concurrent cap by
        construction), skips a ticker already present for this provider, fetches + persists the
        rest, and returns which were fetched vs skipped vs failed. A ticker whose fetch raises a
        labeled :class:`HistoryFetchError` (does not resolve, not entitled, session dropped) is
        logged and recorded in ``failed`` — it never aborts the rest of a large sweep. Persisting
        through ``store.write`` replaces the ``daily_bar`` partitions for a touched
        ``(provider, underlying, trade_date)`` — idempotent on re-run.

        The resume skip is decided against ONE upfront partition-name scan
        (:meth:`ParquetStore.underlyings_present` — a filesystem walk, no Parquet read), never a
        full-table read per ticker: on the live store (hundreds of thousands of one-row files)
        the per-ticker read was the real stall behind the observed ~3 names / 10 min. Storage
        stays idempotent on ``(provider, underlying, trade_date)``, so even a non-skipped
        re-fetch replaces rather than duplicates — the skip is the efficiency, the idempotent
        key is the correctness.
        """
        log = _LOGGER.bind(correlation_id=correlation_id, provider=self.provider)
        fetched: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        bar_count = 0
        present = self.store.underlyings_present("daily_bar", provider=self.provider)
        for request in requests:
            if request.underlying in present:
                skipped.append(request.underlying)
                continue
            try:
                bars = self.fetch(request)
            except HistoryFetchError as exc:
                log.info(
                    "ibkr.history.backfill.ticker_failed",
                    underlying=request.underlying,
                    conid=request.conid,
                    error=str(exc),
                )
                failed.append(request.underlying)
                continue
            if bars:
                self.store.write("daily_bar", list(bars))
                bar_count += len(bars)
            fetched.append(request.underlying)
        log.info(
            "ibkr.history.backfill.done",
            fetched=len(fetched),
            skipped=len(skipped),
            failed=len(failed),
            bar_count=bar_count,
        )
        return BackfillResult(
            fetched=tuple(fetched),
            skipped=tuple(skipped),
            bar_count=bar_count,
            failed=tuple(failed),
        )
