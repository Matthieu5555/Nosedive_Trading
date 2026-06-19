"""The ECB Data Portal rate feed: CSV parsing, instrument->series mapping, and the ingest service.

Network is isolated behind the injected `transport`, so these exercise the parser and the mapping
against fixture CSV bodies and never touch the live portal. Expected rates are derived here from the
same convention converter the ingest uses, not copied from the module.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from algotrading.core.config import CurrencyRateConfig, RatePillarConfig
from algotrading.infra.rates import (
    EcbRateSource,
    EcbRateSourceError,
    ingest_ecb_rates,
    parse_observation_csv,
    to_continuous_act365,
)
from algotrading.infra.rates.ecb_source import SERIES_BY_INSTRUMENT

# A trimmed but faithful ECB `csvdata` body: header line + one observation row.
_ESTR_CSV = (
    "KEY,FREQ,TIME_PERIOD,OBS_VALUE,UNIT_MEASURE\n"
    "EST.B.EU000A2X2A25.WT,B,2026-06-18,2.18,PC\n"
)
_GOVT_3M_CSV = (
    "KEY,FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\n"
    "YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3M,B,U2,2026-06-18,2.2245448167\n"
)

_HASHES = {"rates": "rates-hash-0"}
_CALC = datetime(2026, 6, 18, 17, 45, tzinfo=UTC)


def _eur_config() -> CurrencyRateConfig:
    return CurrencyRateConfig(
        currency="EUR",
        source="ecb_estr_govt_aaa",
        day_count="ACT/365",
        compounding="continuous",
        interpolation="linear_zero",
        pillars=(
            RatePillarConfig(tenor_label="ON", maturity_years=1 / 365, instrument="estr_on"),
            RatePillarConfig(tenor_label="3m", maturity_years=0.25, instrument="govt_3m"),
            RatePillarConfig(tenor_label="6m", maturity_years=0.5, instrument="govt_6m"),
        ),
    )


# --- parser ---------------------------------------------------------------------------------------


def test_parse_observation_csv_returns_date_and_value() -> None:
    assert parse_observation_csv(_ESTR_CSV) == (date(2026, 6, 18), 2.18)


def test_parse_observation_csv_takes_the_latest_of_several_rows() -> None:
    body = (
        "KEY,TIME_PERIOD,OBS_VALUE\n"
        "X,2026-06-16,2.10\n"
        "X,2026-06-18,2.18\n"
        "X,2026-06-17,2.15\n"
    )
    assert parse_observation_csv(body) == (date(2026, 6, 18), 2.18)


def test_parse_observation_csv_rejects_an_empty_body() -> None:
    with pytest.raises(ValueError):
        parse_observation_csv("KEY,TIME_PERIOD,OBS_VALUE\n")


# --- source: mapping + scaling + gaps -------------------------------------------------------------


def _transport_from(bodies: dict[str, str]):
    """Build a transport that returns a fixture body per series key found in the requested URL."""

    def transport(url: str) -> str:
        for instrument, series in SERIES_BY_INSTRUMENT.items():
            if series.key in url and instrument in bodies:
                return bodies[instrument]
        raise OSError(f"no fixture for {url}")

    return transport


def test_fetch_maps_instruments_and_scales_percent_to_decimal() -> None:
    source = EcbRateSource(transport=_transport_from({"estr_on": _ESTR_CSV, "govt_3m": _GOVT_3M_CSV}))
    fetched = source.fetch(["estr_on", "govt_3m"])
    assert fetched.levels["estr_on"] == pytest.approx(0.0218)
    assert fetched.levels["govt_3m"] == pytest.approx(0.022245448167)
    assert fetched.observation_date == date(2026, 6, 18)


def test_fetch_skips_a_series_with_no_data_as_a_coverage_gap() -> None:
    # govt_6m has no fixture -> the transport raises -> that pillar is dropped, not fatal.
    source = EcbRateSource(transport=_transport_from({"estr_on": _ESTR_CSV}))
    fetched = source.fetch(["estr_on", "govt_6m"])
    assert set(fetched.levels) == {"estr_on"}


def test_fetch_raises_when_every_series_fails() -> None:
    source = EcbRateSource(transport=_transport_from({}))
    with pytest.raises(EcbRateSourceError):
        source.fetch(["estr_on", "govt_3m"])


def test_unknown_instrument_is_ignored() -> None:
    source = EcbRateSource(transport=_transport_from({"estr_on": _ESTR_CSV}))
    fetched = source.fetch(["estr_on", "not_a_pillar"])
    assert set(fetched.levels) == {"estr_on"}


# --- ingest service -------------------------------------------------------------------------------


def test_ingest_builds_canonical_points_dated_to_the_observation() -> None:
    source = EcbRateSource(transport=_transport_from({"estr_on": _ESTR_CSV, "govt_3m": _GOVT_3M_CSV}))
    points = ingest_ecb_rates(
        currency_config=_eur_config(),
        config_hashes=_HASHES,
        calc_ts=_CALC,
        source=source,
    )
    by_tenor = {p.pillar_tenor: p for p in points}
    assert set(by_tenor) == {"ON", "3m"}  # 6m had no fixture -> coverage gap
    # The 3m rate matches the standalone converter on the scaled decimal (identity for continuous).
    expected_3m = to_continuous_act365(
        0.022245448167, 0.25, source_day_count="ACT/365", source_compounding="continuous"
    )
    assert by_tenor["3m"].rate == pytest.approx(expected_3m, abs=1e-12)
    # as_of defaults to the ECB observation date (the no-look-ahead publication date).
    assert all(p.as_of == date(2026, 6, 18) for p in points)
    assert by_tenor["3m"].diagnostics.source == "ecb_estr_govt_aaa"
    assert by_tenor["3m"].provenance.source_records[0].table == "ecb_data_portal"
