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
    """What a backfill run did: which tickers were fetched vs skipped (already on disk)."""

    fetched: tuple[str, ...]
    skipped: tuple[str, ...]
    bar_count: int

    @property
    def attempted(self) -> tuple[str, ...]:
        return tuple(sorted({*self.fetched, *self.skipped}))


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
        self.transport.get(_HISTORY_PATH, {"conid": "0", "period": "1d", "bar": self.config.bar})
        self._warmed_up = True

    def fetch(self, request: HistoryRequest) -> tuple[DailyBar, ...]:
        """Fetch and normalize one ticker's daily bars (established-gated, retried).

        Refuses to fire unless the brokerage session is established (defers with a labeled
        :class:`HistoryFetchError` otherwise — never a request into a dead session). Wraps the
        GET in the config's exponential-with-cap retry for IBKR maintenance-window overlap.
        Returns the normalized :class:`DailyBar` tuple (empty for a ticker with no history in
        the window).
        """
        if not self.is_established():
            raise HistoryFetchError(
                f"brokerage session not established; deferring history fetch for "
                f"{request.underlying!r}"
            )
        self.warmup()
        payload = self._get_with_retry(request)
        return history_to_daily_bars(
            payload,
            provider=self.provider,
            underlying=request.underlying,
            bar_type=self.bar_type,
            source=self.source,
            provenance_for=lambda trade_date: self.provenance_for(request.underlying, trade_date),
        )

    def _get_with_retry(self, request: HistoryRequest) -> dict[str, Any]:
        """GET the history endpoint with exponential-with-cap retry around transient failures."""
        params = {"conid": str(request.conid), "period": request.period, "bar": self.config.bar}
        last_error: Exception | None = None
        for attempt in range(self.config.retry.max_attempts):
            try:
                payload = self.transport.get(_HISTORY_PATH, params)
            except Exception as exc:  # transport-level failure (maintenance window, timeout)
                last_error = exc
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

    def _already_on_disk(self, request: HistoryRequest) -> bool:
        """Whether this ticker already has bars on disk for this provider (resume skip).

        A backfill is resumable: a ticker whose ``(provider, underlying)`` already has any
        ``daily_bar`` partition is treated as done for the window and skipped, so a re-run
        re-fetches only the missing tail. Storage is idempotent on
        ``(provider, underlying, trade_date)``, so even a non-skipped re-fetch replaces rather
        than duplicates — the skip is the efficiency, the idempotent key is the correctness.
        """
        existing = self.store.read("daily_bar", underlying=None, provider=self.provider)
        return any(bar.underlying == request.underlying for bar in existing)

    def backfill(
        self, requests: Sequence[HistoryRequest], *, correlation_id: str = ""
    ) -> BackfillResult:
        """Backfill every requested ticker, skipping those already on disk (resumable).

        Iterates the requests (one ticker at a time, honouring the 5-concurrent cap by
        construction), skips a ticker already present for this provider, fetches + persists the
        rest, and returns which were fetched vs skipped. Persisting through ``store.write``
        replaces the ``daily_bar`` partitions for a touched ``(provider, underlying,
        trade_date)`` — idempotent on re-run.
        """
        log = _LOGGER.bind(correlation_id=correlation_id, provider=self.provider)
        fetched: list[str] = []
        skipped: list[str] = []
        bar_count = 0
        for request in requests:
            if self._already_on_disk(request):
                skipped.append(request.underlying)
                continue
            bars = self.fetch(request)
            if bars:
                self.store.write("daily_bar", list(bars))
                bar_count += len(bars)
            fetched.append(request.underlying)
        log.info(
            "ibkr.history.backfill.done",
            fetched=len(fetched),
            skipped=len(skipped),
            bar_count=bar_count,
        )
        return BackfillResult(
            fetched=tuple(fetched), skipped=tuple(skipped), bar_count=bar_count
        )
