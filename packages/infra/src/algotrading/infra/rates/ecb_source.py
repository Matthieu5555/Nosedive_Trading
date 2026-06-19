"""Fetch the EUR risk-free pillar levels from the ECB Data Portal and persist them (ADR 0054).

This is the live feed that finally populates the `rates` table the curve evaluator and Rho basis
read. It maps each configured pillar `instrument` to one ECB Data Portal series, fetches the latest
observation, and hands the levels to `build_rate_points` (which converts to the canonical
continuous-ACT/365 zero rate and stamps provenance). A pillar whose series the portal has no data
for is **skipped** — a coverage gap, exactly as `build_rate_points` already treats a missing level,
not a failure.

Why these series (all free, daily, no API key, one official publisher — the ECB):

  * ``estr_on``  — the Euro Short-Term Rate (€STR), the canonical EUR overnight risk-free rate.
  * ``govt_3m`` … ``govt_3y`` — nodes of the ECB euro-area **AAA-government spot** yield curve
    (Svensson-fit zero-coupon spot rates, which are continuously compounded by construction).

The €STR (overnight) and the AAA spot curve (term) are both published by the ECB and are a standard,
defensible EUR risk-free curve. They are NOT the Euribor/OIS quotes a desk might prefer for
discounting; Euribor is not redistributed on the ECB's free portal. The two differ by roughly tens
of basis points at the front; the warn-only implied-vs-riskfree spread QC (ADR 0054 RULED 5) is the
backstop. Swap `SERIES_BY_INSTRUMENT` (and the matching pillar labels in `configs/rates.yaml`) if a
Euribor/OIS feed becomes available.

Network access is isolated behind the `Transport` callable so the parser and mapping are unit-tested
with fixture CSV and never touch the network.
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

from algotrading.core.config import CurrencyRateConfig
from algotrading.core.provenance import SourceRecordRef
from algotrading.infra.contracts import RiskFreeRatePoint

from .ingest import RATES_VERSION, build_rate_points

_PORTAL = "https://data-api.ecb.europa.eu/service/data"
_HTTP_TIMEOUT_S = 20.0
_USER_AGENT = "algotrading-rates-ingest/1.0"


@dataclass(frozen=True, slots=True)
class EcbSeries:
    """One ECB Data Portal series feeding a configured pillar `instrument`.

    `dataset`/`key` address the series (the CSV endpoint is `…/data/{dataset}/{key}`); `scale`
    converts the published unit to a decimal rate (ECB quotes percent, so 0.01). `description` is
    carried only for provenance/readability.
    """

    dataset: str
    key: str
    scale: float
    description: str

    @property
    def url(self) -> str:
        return f"{_PORTAL}/{self.dataset}/{self.key}?lastNObservations=1&format=csvdata"


# instrument label (configs/rates.yaml) -> ECB series. Keep in lock-step with the EUR pillar set.
SERIES_BY_INSTRUMENT: Mapping[str, EcbSeries] = {
    "estr_on": EcbSeries("EST", "B.EU000A2X2A25.WT", 0.01, "€STR overnight"),
    "govt_3m": EcbSeries("YC", "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3M", 0.01, "AAA-govt spot 3M"),
    "govt_6m": EcbSeries("YC", "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_6M", 0.01, "AAA-govt spot 6M"),
    "govt_12m": EcbSeries("YC", "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_1Y", 0.01, "AAA-govt spot 1Y"),
    "govt_2y": EcbSeries("YC", "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y", 0.01, "AAA-govt spot 2Y"),
    "govt_3y": EcbSeries("YC", "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3Y", 0.01, "AAA-govt spot 3Y"),
}

# A url -> CSV-text fetcher. Injected in tests; defaults to urllib so the module needs no live deps.
Transport = Callable[[str], str]


class EcbRateSourceError(RuntimeError):
    """The ECB feed could not produce a single usable pillar level."""


def _urllib_get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:  # noqa: S310
        return response.read().decode("utf-8")


def parse_observation_csv(text: str) -> tuple[date, float]:
    """Parse an ECB `csvdata` response into its latest `(observation_date, raw_value)`.

    The portal returns one row per observation with `TIME_PERIOD` (ISO date) and `OBS_VALUE`
    columns. With `lastNObservations=1` there is a single data row; we still take the latest row
    defensively. Raises `ValueError` on an empty or malformed body so the caller can treat the
    series as a coverage gap.
    """
    reader = csv.DictReader(io.StringIO(text))
    rows = [row for row in reader if (row.get("OBS_VALUE") or "").strip()]
    if not rows:
        raise ValueError("ECB response carried no observation with a value")
    latest = max(rows, key=lambda r: (r.get("TIME_PERIOD") or ""))
    return (
        date.fromisoformat((latest["TIME_PERIOD"] or "")[:10]),
        float(latest["OBS_VALUE"]),
    )


@dataclass(frozen=True, slots=True)
class FetchedLevels:
    """The pillar levels one ECB pull produced, with the latest observation date across them."""

    levels: dict[str, float]
    observation_date: date


@dataclass(frozen=True, slots=True)
class EcbRateSource:
    """Pulls the configured pillar levels from the ECB Data Portal (one HTTP GET per series)."""

    transport: Transport = _urllib_get

    def fetch(self, instruments: Iterable[str]) -> FetchedLevels:
        """Fetch each instrument's latest level (as a decimal rate), skipping series with no data.

        A series the portal 404s or returns empty for is dropped (coverage gap). A genuine transport
        failure (network down, 5xx) is also dropped per-series, but if **every** requested series
        fails the call raises `EcbRateSourceError` rather than silently returning an empty curve.
        """
        levels: dict[str, float] = {}
        observed: list[date] = []
        for instrument in instruments:
            series = SERIES_BY_INSTRUMENT.get(instrument)
            if series is None:
                continue
            try:
                obs_date, raw = parse_observation_csv(self.transport(series.url))
            except (urllib.error.URLError, ValueError, OSError):
                continue
            levels[instrument] = raw * series.scale
            observed.append(obs_date)
        if not levels:
            raise EcbRateSourceError(
                "the ECB feed returned no usable level for any requested pillar instrument"
            )
        return FetchedLevels(levels=levels, observation_date=max(observed))


def ingest_ecb_rates(
    *,
    currency_config: CurrencyRateConfig,
    config_hashes: Mapping[str, str],
    calc_ts: datetime,
    source: EcbRateSource | None = None,
    as_of: date | None = None,
) -> tuple[RiskFreeRatePoint, ...]:
    """Fetch the ECB pillar levels and build the canonical `RiskFreeRatePoint` rows for a currency.

    `as_of` defaults to the ECB observation date carried by the fetch (the publication date the
    no-look-ahead read filters on); pass it explicitly only to override. `calc_ts` and the config
    hashes are the caller's provenance inputs, exactly as `build_rate_points` expects.
    """
    src = source or EcbRateSource()
    instruments = [pillar.instrument for pillar in currency_config.pillars]
    fetched = src.fetch(instruments)
    effective_as_of = as_of or fetched.observation_date
    snapshot_ts = datetime.combine(
        fetched.observation_date, datetime.min.time(), tzinfo=calc_ts.tzinfo
    )
    source_records = tuple(
        SourceRecordRef(table="ecb_data_portal", primary_key=(SERIES_BY_INSTRUMENT[i].key,))
        for i in fetched.levels
    )
    return build_rate_points(
        currency_config=currency_config,
        published_levels=fetched.levels,
        as_of=effective_as_of,
        snapshot_ts=snapshot_ts,
        source_snapshot_ts=snapshot_ts,
        calc_ts=calc_ts,
        config_hashes=config_hashes,
        source_records=source_records,
    )


__all__ = [
    "EcbRateSource",
    "EcbRateSourceError",
    "EcbSeries",
    "FetchedLevels",
    "RATES_VERSION",
    "SERIES_BY_INSTRUMENT",
    "Transport",
    "ingest_ecb_rates",
    "parse_observation_csv",
]
