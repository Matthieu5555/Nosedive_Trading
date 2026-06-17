from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MARGIN_CAPACITY_VERSION = "margin-capacity-1.0.0"


class MarginCapacityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MarginCapacityConfig:

    version: str
    investing_working_capital: float
    initial_margin_fraction: float = 1.0
    premium_offsets_margin: bool = False
    headroom_floor: float = 0.0

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise MarginCapacityError("version must be non-empty")
        iwc = self.investing_working_capital
        if not (math.isfinite(iwc) and iwc >= 0.0):
            raise MarginCapacityError(
                f"investing_working_capital must be finite and non-negative, "
                f"got {self.investing_working_capital}"
            )
        if not (0.0 < self.initial_margin_fraction <= 1.0):
            raise MarginCapacityError(
                f"initial_margin_fraction must lie in (0, 1], got {self.initial_margin_fraction}"
            )
        if not (math.isfinite(self.headroom_floor) and self.headroom_floor >= 0.0):
            raise MarginCapacityError(
                f"headroom_floor must be finite and non-negative, got {self.headroom_floor}"
            )

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> MarginCapacityConfig:
        return cls(
            version=str(section.get("version", MARGIN_CAPACITY_VERSION)),
            investing_working_capital=float(section["investing_working_capital"]),
            initial_margin_fraction=float(section.get("initial_margin_fraction", 1.0)),
            premium_offsets_margin=bool(section.get("premium_offsets_margin", False)),
            headroom_floor=float(section.get("headroom_floor", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class ShortPutLine:

    contract_key: str
    open_contracts: float
    strike: float
    multiplier: float
    premium_per_unit: float = 0.0

    def __post_init__(self) -> None:
        if not self.contract_key.strip():
            raise MarginCapacityError("contract_key must be non-empty")
        for name in ("open_contracts", "strike", "multiplier", "premium_per_unit"):
            value = getattr(self, name)
            if not math.isfinite(value):
                raise MarginCapacityError(f"{name} must be finite, got {value}")
        if self.open_contracts < 0.0:
            raise MarginCapacityError(
                f"open_contracts must be non-negative, got {self.open_contracts}"
            )
        if self.strike <= 0.0:
            raise MarginCapacityError(f"strike must be positive, got {self.strike}")
        if self.multiplier <= 0.0:
            raise MarginCapacityError(f"multiplier must be positive, got {self.multiplier}")
        if self.premium_per_unit < 0.0:
            raise MarginCapacityError(
                f"premium_per_unit must be non-negative, got {self.premium_per_unit}"
            )


@dataclass(frozen=True, slots=True)
class ProspectiveLine:

    strike: float
    multiplier: float
    premium_per_unit: float = 0.0

    def __post_init__(self) -> None:
        for name in ("strike", "multiplier", "premium_per_unit"):
            value = getattr(self, name)
            if not math.isfinite(value):
                raise MarginCapacityError(f"{name} must be finite, got {value}")
        if self.strike <= 0.0:
            raise MarginCapacityError(f"strike must be positive, got {self.strike}")
        if self.multiplier <= 0.0:
            raise MarginCapacityError(f"multiplier must be positive, got {self.multiplier}")
        if self.premium_per_unit < 0.0:
            raise MarginCapacityError(
                f"premium_per_unit must be non-negative, got {self.premium_per_unit}"
            )


@dataclass(frozen=True, slots=True)
class MarginCapacityForecast:

    version: str
    investing_working_capital: float
    consumed_margin: float
    remaining_headroom: float
    lines_open: float
    additional_lines: int
    next_line_margin: float
    at_capacity: bool
    over_capacity: bool


def line_initial_margin(
    *, strike: float, multiplier: float, premium_per_unit: float, config: MarginCapacityConfig
) -> float:
    assignment_obligation = strike * multiplier * config.initial_margin_fraction
    if config.premium_offsets_margin:
        assignment_obligation -= premium_per_unit * multiplier
    return max(assignment_obligation, 0.0)


def _consumed_margin(lines: Sequence[ShortPutLine], config: MarginCapacityConfig) -> float:
    return math.fsum(
        line.open_contracts
        * line_initial_margin(
            strike=line.strike,
            multiplier=line.multiplier,
            premium_per_unit=line.premium_per_unit,
            config=config,
        )
        for line in lines
    )


def forecast_capacity(
    open_lines: Sequence[ShortPutLine],
    *,
    config: MarginCapacityConfig,
    next_line: ProspectiveLine | None = None,
) -> MarginCapacityForecast:
    consumed = _consumed_margin(open_lines, config)
    usable = config.investing_working_capital - config.headroom_floor
    remaining = usable - consumed
    lines_open = math.fsum(line.open_contracts for line in open_lines)

    if next_line is None:
        next_margin = 0.0
        additional = 0
    else:
        next_margin = line_initial_margin(
            strike=next_line.strike,
            multiplier=next_line.multiplier,
            premium_per_unit=next_line.premium_per_unit,
            config=config,
        )
        if next_margin <= 0.0:
            raise MarginCapacityError(
                "prospective line carries non-positive margin; cannot size headroom against it"
            )
        additional = int(math.floor(remaining / next_margin)) if remaining > 0.0 else 0

    over = remaining < 0.0
    at_cap = (not over) and (next_line is not None) and additional == 0
    return MarginCapacityForecast(
        version=config.version,
        investing_working_capital=config.investing_working_capital,
        consumed_margin=consumed,
        remaining_headroom=remaining,
        lines_open=lines_open,
        additional_lines=max(additional, 0),
        next_line_margin=next_margin,
        at_capacity=at_cap,
        over_capacity=over,
    )


def line_capacity_cap(
    *, config: MarginCapacityConfig, representative_line: ProspectiveLine
) -> int:
    forecast = forecast_capacity((), config=config, next_line=representative_line)
    return forecast.additional_lines
