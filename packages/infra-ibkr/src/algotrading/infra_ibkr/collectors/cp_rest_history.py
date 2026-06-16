from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog
from algotrading.infra.contracts import DailyBar
from algotrading.infra.storage import ParquetStore
from tenacity import RetryCallState, RetryError, Retrying, retry_if_exception, stop_after_attempt

from ..config import IbkrHistoryConfig
from ..connectivity.cp_rest_transport import SupportsRestGet
from .cp_rest_history_normalize import history_to_daily_bars

_LOGGER = structlog.get_logger("ibkr.history")

_HISTORY_PATH = "/iserver/marketdata/history"

_TERMINAL_WINDOW_STATUSES = frozenset({404, 500})


def _window_http_status(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


class HistoryFetchError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class HistoryRequest:

    underlying: str
    conid: int
    period: str


@dataclass(frozen=True, slots=True)
class BackfillResult:

    fetched: tuple[str, ...]
    skipped: tuple[str, ...]
    bar_count: int
    failed: tuple[str, ...] = ()
    refreshed: tuple[str, ...] = ()

    @property
    def attempted(self) -> tuple[str, ...]:
        return tuple(sorted({*self.fetched, *self.refreshed, *self.skipped, *self.failed}))


@dataclass
class CpRestHistoryCollector:

    transport: SupportsRestGet
    store: ParquetStore
    config: IbkrHistoryConfig
    provider: str
    is_established: Callable[[], bool]
    provenance_for: Callable[[str, date], object]
    sleep: Callable[[float], None]
    bar_type: str = "1d-TRADES"
    source: str = "cp-rest-history"
    max_history_windows: int = 40
    _warmed_up: bool = field(default=False, init=False)

    def warmup(self) -> None:
        if self._warmed_up or not self.config.warmup_required:
            self._warmed_up = True
            return
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

    def fetch(self, request: HistoryRequest, *, recent_only: bool = False) -> tuple[DailyBar, ...]:
        if not self.is_established():
            raise HistoryFetchError(
                f"brokerage session not established; deferring history fetch for "
                f"{request.underlying!r}"
            )
        self.warmup()
        if recent_only:
            return self._fetch_recent_window(request)
        return self._fetch_all_windows(request)

    def _normalize(self, payload: dict[str, Any], request: HistoryRequest) -> tuple[DailyBar, ...]:
        return history_to_daily_bars(
            payload,
            provider=self.provider,
            underlying=request.underlying,
            bar_type=self.bar_type,
            source=self.source,
            provenance_for=lambda d: self.provenance_for(request.underlying, d),
        )

    def _fetch_recent_window(self, request: HistoryRequest) -> tuple[DailyBar, ...]:
        payload = self._get_with_retry(request, start_time=None)
        bars = self._normalize(payload, request)
        return tuple(sorted(bars, key=lambda bar: bar.trade_date))

    def _fetch_all_windows(self, request: HistoryRequest) -> tuple[DailyBar, ...]:
        by_date: dict[date, DailyBar] = {}
        start_time: str | None = None
        for _window in range(self.max_history_windows):
            try:
                payload = self._get_with_retry(request, start_time=start_time)
            except HistoryFetchError:
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
            bars = self._normalize(payload, request)
            if not any(bar.trade_date not in by_date for bar in bars):
                break
            by_date.update({bar.trade_date: bar for bar in bars})
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
        params = {"conid": str(request.conid), "period": request.period, "bar": self.config.bar}
        if start_time is not None:
            params["startTime"] = start_time

        def attempt_get() -> Any:
            try:
                return self.transport.get(_HISTORY_PATH, params)
            except Exception as exc:
                status = _window_http_status(exc)
                if status in _TERMINAL_WINDOW_STATUSES:
                    raise HistoryFetchError(
                        f"history window for {request.underlying!r} returned HTTP {status} "
                        f"(no data for the requested window)"
                    ) from exc
                raise

        def _wait(retry_state: RetryCallState) -> float:
            return self.config.retry.delay_for(retry_state.attempt_number - 1)

        retrying = Retrying(
            retry=retry_if_exception(lambda exc: not isinstance(exc, HistoryFetchError)),
            stop=stop_after_attempt(self.config.retry.max_attempts),
            wait=_wait,
            sleep=self.sleep,
        )
        try:
            payload = retrying(attempt_get)
        except RetryError as err:
            last_error = err.last_attempt.exception()
            raise HistoryFetchError(
                f"history fetch for {request.underlying!r} failed after "
                f"{self.config.retry.max_attempts} attempts: {last_error}"
            ) from last_error
        if not isinstance(payload, dict):
            raise HistoryFetchError(
                f"history payload for {request.underlying!r} is not a mapping: {payload!r}"
            )
        return payload

    def backfill(
        self,
        requests: Sequence[HistoryRequest],
        *,
        correlation_id: str = "",
        refresh_tail: bool = False,
    ) -> BackfillResult:
        log = _LOGGER.bind(correlation_id=correlation_id, provider=self.provider)
        fetched: list[str] = []
        refreshed: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        bar_count = 0
        present = self.store.underlyings_present("daily_bar", provider=self.provider)
        for request in requests:
            already_present = request.underlying in present
            if already_present and not refresh_tail:
                skipped.append(request.underlying)
                continue
            try:
                bars = self.fetch(request, recent_only=already_present)
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
            (refreshed if already_present else fetched).append(request.underlying)
        log.info(
            "ibkr.history.backfill.done",
            fetched=len(fetched),
            refreshed=len(refreshed),
            skipped=len(skipped),
            failed=len(failed),
            bar_count=bar_count,
        )
        return BackfillResult(
            fetched=tuple(fetched),
            skipped=tuple(skipped),
            bar_count=bar_count,
            failed=tuple(failed),
            refreshed=tuple(refreshed),
        )
