from __future__ import annotations

import zoneinfo
from dataclasses import dataclass, field
from datetime import date

import exchange_calendars as xcals
from algotrading.core.config import ConfigError
from algotrading.infra.contracts import (
    SURFACE_SIDE_COMBINED,
    MarketStateSnapshot,
    ProjectedOptionAnalytics,
)
from algotrading.infra.snapshots import is_valid_two_sided
from algotrading.infra.universe import (
    CalendarResolver,
    IndexRegistryError,
    load_index_registry,
)
from algotrading.infra.universe.calendar_resolver import CalendarResolutionError

from .context import AppContext
from .sci_format import UNITS, sci_unit
from .store_reads import read_for_underlying

MODE_STRICT = "strict"
MODE_INDICATIVE = "indicative"

_REFERENCE_TENOR_YEARS = 0.25
_RR_DELTA = 0.25
_IV_SANE_MIN = 0.0
_IV_SANE_MAX = 0.6
_CANONICAL_FIELD_COUNT = 9
_RIGHT_SLOT = 8


def _is_sane_iv(value: float | None) -> bool:
    return value is not None and _IV_SANE_MIN < value <= _IV_SANE_MAX


@dataclass(frozen=True, slots=True)
class Fact:
    fact_id: str
    label: str
    value_text: str
    raw_value: float | None
    unit: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.fact_id,
            "label": self.label,
            "value_text": self.value_text,
            "raw_value": self.raw_value,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class Coverage:
    option_rows: int
    two_sided: int
    excluded: int
    two_sided_fraction: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "option_rows": self.option_rows,
            "two_sided": self.two_sided,
            "excluded": self.excluded,
            "two_sided_fraction": self.two_sided_fraction,
        }


@dataclass(frozen=True, slots=True)
class Frame:
    underlying: str
    trade_date: date | None
    close_instant: str | None
    mode: str
    coverage: Coverage
    run_id: str | None = None

    def coverage_label(self) -> str | None:
        if self.coverage.option_rows <= 0:
            return None
        return f"{self.coverage.two_sided}/{self.coverage.option_rows} cotations"

    def to_dict(self) -> dict[str, object]:
        return {
            "underlying": self.underlying,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "run_id": self.run_id,
            "close_instant": self.close_instant,
            "mode": self.mode,
            "indicative": self.mode == MODE_INDICATIVE,
            "coverage": self.coverage.to_dict(),
            "coverage_label": self.coverage_label(),
        }


@dataclass(frozen=True, slots=True)
class GroundingContext:
    frame: Frame
    facts: list[Fact] = field(default_factory=list)
    tenor_label: str | None = None
    is_reference_tenor: bool = False

    def fact_values(self) -> list[str]:
        return [f.value_text for f in self.facts]

    def citations(self) -> list[dict[str, object]]:
        mode_word = (
            "indicative signal"
            if self.frame.mode == MODE_INDICATIVE
            else "recorded signal"
        )
        source = f"{mode_word} · {self.tenor_label}" if self.tenor_label else mode_word
        return [
            {
                "id": fact.fact_id,
                "label": fact.label,
                "value": fact.value_text,
                "source": source,
            }
            for fact in self.facts
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "frame": self.frame.to_dict(),
            "tenor_label": self.tenor_label,
            "is_reference_tenor": self.is_reference_tenor,
            "facts": [f.to_dict() for f in self.facts],
        }


def resolve_close_instant(
    ctx: AppContext, underlying: str, trade_date: date | None
) -> str | None:
    """The option settlement close as a PM-legible local time-of-day, e.g. "17:30 CEST".

    The single source of truth for the close instant the whole front renders: the calendar code and
    the option_settlement_close time-of-day come from the index registry (configs/universe.yaml),
    the venue zone and its winter/summer abbreviation come from that calendar — never a hard-coded
    "17:30 CET" constant. Per-date so the abbreviation is honest (CET in winter, CEST in summer).
    The date already travels separately (the front's as-of), so this is the time-of-day + zone only.
    Returns None when the registry, calendar, or session is unavailable (additive-nullable; the
    front degrades to a date-only as-of, never a guessed instant).
    """
    if trade_date is None:
        return None
    try:
        registry = load_index_registry(ctx.configs_dir)
        resolver = CalendarResolver(registry, as_of=trade_date)
        close_utc = resolver.session_close(underlying, trade_date)
        calendar_code = registry.get(underlying).calendar
        venue_zone = zoneinfo.ZoneInfo(str(xcals.get_calendar(calendar_code).tz))
    except (ConfigError, IndexRegistryError, CalendarResolutionError, KeyError):
        return None
    local_close = close_utc.astimezone(venue_zone)
    return f"{local_close.strftime('%H:%M')} {local_close.tzname()}"


def _is_option_snapshot(snapshot: MarketStateSnapshot) -> bool:
    fields = snapshot.instrument_key.split("|")
    if len(fields) != _CANONICAL_FIELD_COUNT:
        return False
    return fields[_RIGHT_SLOT] in ("C", "P")


def coverage_from_snapshots(snapshots: list[MarketStateSnapshot]) -> Coverage:
    options = [s for s in snapshots if _is_option_snapshot(s)]
    option_rows = len(options)
    two_sided = sum(1 for s in options if is_valid_two_sided(s.bid, s.ask))
    excluded = option_rows - two_sided
    fraction = (two_sided / option_rows) if option_rows > 0 else None
    return Coverage(
        option_rows=option_rows,
        two_sided=two_sided,
        excluded=excluded,
        two_sided_fraction=fraction,
    )


def _reference_maturity(
    cells: list[ProjectedOptionAnalytics],
) -> list[ProjectedOptionAnalytics]:
    combined = [c for c in cells if c.surface_side == SURFACE_SIDE_COMBINED]
    if not combined:
        return []
    target = min(
        {c.maturity_years for c in combined},
        key=lambda m: abs(m - _REFERENCE_TENOR_YEARS),
    )
    return [c for c in combined if c.maturity_years == target]


def _atm_iv(slice_cells: list[ProjectedOptionAnalytics]) -> float | None:
    best: float | None = None
    best_abs = float("inf")
    for cell in slice_cells:
        if not _is_sane_iv(cell.implied_vol):
            continue
        if abs(cell.log_moneyness) < best_abs:
            best_abs = abs(cell.log_moneyness)
            best = cell.implied_vol
    return best


def _iv_at_delta(slice_cells: list[ProjectedOptionAnalytics], delta: float) -> float | None:
    sane = [c for c in slice_cells if _is_sane_iv(c.implied_vol)]
    if not sane:
        return None
    for cell in sane:
        if abs(cell.target_delta - delta) < 1e-9:
            return cell.implied_vol
    lo: tuple[float, float] | None = None
    hi: tuple[float, float] | None = None
    for cell in sane:
        if cell.target_delta <= delta and (lo is None or cell.target_delta > lo[0]):
            lo = (cell.target_delta, cell.implied_vol)
        if cell.target_delta >= delta and (hi is None or cell.target_delta < hi[0]):
            hi = (cell.target_delta, cell.implied_vol)
    if lo is None or hi is None:
        return None
    if lo[0] == hi[0]:
        return lo[1]
    t = (delta - lo[0]) / (hi[0] - lo[0])
    return lo[1] + t * (hi[1] - lo[1])


def _scorecard_facts(
    slice_cells: list[ProjectedOptionAnalytics],
) -> list[Fact]:
    facts: list[Fact] = []
    atm = _atm_iv(slice_cells)
    facts.append(
        Fact(
            fact_id="atm_level",
            label="ATM (at-the-money implied vol)",
            value_text=sci_unit(atm, UNITS["vol"]),
            raw_value=atm,
            unit=UNITS["vol"],
        )
    )
    iv_put = _iv_at_delta(slice_cells, -_RR_DELTA)
    iv_call = _iv_at_delta(slice_cells, _RR_DELTA)
    skew = iv_put - iv_call if iv_put is not None and iv_call is not None else None
    facts.append(
        Fact(
            fact_id="skew_25d",
            label="Skew 25Δ (put 25Δ − call 25Δ)",
            value_text=sci_unit(skew, UNITS["vol"]),
            raw_value=skew,
            unit=UNITS["vol"],
        )
    )
    convexity = (
        iv_put + iv_call - 2.0 * atm
        if iv_put is not None and iv_call is not None and atm is not None
        else None
    )
    facts.append(
        Fact(
            fact_id="convexity_25d",
            label="Convexity 25Δ (butterfly)",
            value_text=sci_unit(convexity, UNITS["vol"]),
            raw_value=convexity,
            unit=UNITS["vol"],
        )
    )
    return facts


def _coverage_facts(coverage: Coverage) -> list[Fact]:
    fraction_text = (
        f"{coverage.two_sided}/{coverage.option_rows} two-sided quotes"
        if coverage.option_rows > 0
        else "no option quotes"
    )
    return [
        Fact(
            fact_id="surface_coverage",
            label="Surface coverage",
            value_text=fraction_text,
            raw_value=coverage.two_sided_fraction,
            unit=None,
        ),
        Fact(
            fact_id="excluded_rows",
            label="Excluded quotes (one-sided / missing)",
            value_text=f"{coverage.excluded} excluded",
            raw_value=float(coverage.excluded),
            unit=None,
        ),
    ]


def _smile_point_facts(
    slice_cells: list[ProjectedOptionAnalytics], tenor_label: str
) -> list[Fact]:
    facts: list[Fact] = []
    seen: set[float] = set()
    for cell in sorted(slice_cells, key=lambda c: c.target_delta):
        if cell.target_delta in seen or not _is_sane_iv(cell.implied_vol):
            continue
        seen.add(cell.target_delta)
        facts.append(
            Fact(
                fact_id=f"smile_point_{tenor_label}_{cell.delta_band}",
                label=f"Point smile {tenor_label} {cell.delta_band} (Δ={cell.target_delta:+.2f})",
                value_text=sci_unit(cell.implied_vol, UNITS["vol"]),
                raw_value=cell.implied_vol,
                unit=UNITS["vol"],
            )
        )
    return facts


def build_grounding_context(
    ctx: AppContext,
    underlying: str | None,
    trade_date: date | None,
    *,
    mode: str = MODE_STRICT,
    run_id: str | None = None,
) -> GroundingContext:
    resolved_underlying = underlying or ctx.default_underlying
    resolved_mode = MODE_INDICATIVE if mode == MODE_INDICATIVE else MODE_STRICT

    cells: list[ProjectedOptionAnalytics] = read_for_underlying(
        ctx.store, "projected_option_analytics", resolved_underlying, trade_date=trade_date
    )
    snapshots: list[MarketStateSnapshot] = read_for_underlying(
        ctx.store, "market_state_snapshots", resolved_underlying, trade_date=trade_date
    )

    coverage = coverage_from_snapshots(snapshots)
    close_instant = resolve_close_instant(ctx, resolved_underlying, trade_date)
    frame = Frame(
        underlying=resolved_underlying,
        trade_date=trade_date,
        close_instant=close_instant,
        mode=resolved_mode,
        coverage=coverage,
        run_id=run_id,
    )

    slice_cells = _reference_maturity(cells)
    facts: list[Fact] = []
    facts.extend(_coverage_facts(coverage))
    tenor_label: str | None = None
    is_reference = False
    if slice_cells:
        tenor_label = slice_cells[0].tenor_label
        is_reference = abs(slice_cells[0].maturity_years - _REFERENCE_TENOR_YEARS) < 1e-6
        facts.extend(_scorecard_facts(slice_cells))
        facts.extend(_smile_point_facts(slice_cells, tenor_label))

    return GroundingContext(
        frame=frame,
        facts=facts,
        tenor_label=tenor_label,
        is_reference_tenor=is_reference,
    )
