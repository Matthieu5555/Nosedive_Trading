from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

LIQUIDITY_VERSION = "liquidity-1.0.0"

# Why a participation-rate model and not open interest:
# the only captured liquidity field on an option contract is per-snapshot traded
# `volume` (`MarketStateSnapshot.volume`, nullable — see contracts/tables.py). There
# is NO `open_interest` field anywhere in the contract registry, so this module
# screens exit difficulty against *captured traded volume* only and never against
# open interest. When the captured volume is absent (the field is `None`), the flag
# is honestly UNKNOWN rather than silently "ok" — see `LiquidityStatus`.

STATUS_OK = "ok"
STATUS_INEXITABLE = "inexitable"
STATUS_UNKNOWN = "unknown_volume"

LIQUIDITY_STATUSES = (STATUS_OK, STATUS_INEXITABLE, STATUS_UNKNOWN)


class LiquidityError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LiquidityConfig:
    """Bounds the exit-difficulty screen.

    ``participation_rate`` is the fraction of a session's traded volume the desk is
    willing to be (e.g. 0.10 = "we will not be more than 10% of the tape"). ``max_exit_sessions``
    is how many sessions an exit may take before the position is flagged inexitable.
    A position needing more than ``max_exit_sessions`` sessions at the allowed
    participation rate cannot be unwound inside the bound and is flagged.
    """

    version: str = LIQUIDITY_VERSION
    participation_rate: float = 0.10
    max_exit_sessions: float = 1.0

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise LiquidityError("version must be non-empty")
        if not (0.0 < self.participation_rate <= 1.0):
            raise LiquidityError(
                f"participation_rate must lie in (0, 1], got {self.participation_rate}"
            )
        if not (math.isfinite(self.max_exit_sessions) and self.max_exit_sessions > 0.0):
            raise LiquidityError(
                f"max_exit_sessions must be finite and positive, got {self.max_exit_sessions}"
            )


@dataclass(frozen=True, slots=True)
class PositionLiquidity:
    """One position's exit-difficulty screen against captured traded volume.

    ``captured_volume`` is the per-contract traded volume the screen ran against, or
    ``None`` when no volume was captured for the contract (in which case ``status`` is
    ``unknown_volume`` and ``exit_sessions`` is ``None`` — the screen abstains rather
    than asserting a position is liquid on missing data).
    """

    liquidity_version: str
    contract_key: str
    position_size: float
    captured_volume: float | None
    participation_rate: float
    max_exit_sessions: float
    exit_sessions: float | None
    status: str

    @property
    def inexitable(self) -> bool:
        return self.status == STATUS_INEXITABLE


def position_liquidity(
    *,
    contract_key: str,
    position_size: float,
    captured_volume: float | None,
    config: LiquidityConfig,
) -> PositionLiquidity:
    """Flag whether ``position_size`` lots can be exited inside the configured bound.

    Exit sessions = ``|position_size| / (participation_rate * captured_volume)`` — the
    number of sessions needed to unwind the position while staying under the allowed
    share of each session's tape. A position needing more than ``max_exit_sessions``
    is flagged ``inexitable``.

    If ``captured_volume`` is ``None`` (no volume captured for this contract) the screen
    returns ``unknown_volume`` and does not pretend the position is liquid. A captured
    volume of exactly ``0`` (a real session with no trades) is treated as a hard
    ``inexitable`` — you cannot exit into a market that did not trade.
    """
    if not contract_key.strip():
        raise LiquidityError("contract_key must be non-empty")
    if not math.isfinite(position_size):
        raise LiquidityError(f"position_size must be finite, got {position_size}")

    if captured_volume is None:
        return PositionLiquidity(
            liquidity_version=config.version,
            contract_key=contract_key,
            position_size=position_size,
            captured_volume=None,
            participation_rate=config.participation_rate,
            max_exit_sessions=config.max_exit_sessions,
            exit_sessions=None,
            status=STATUS_UNKNOWN,
        )

    if not (math.isfinite(captured_volume) and captured_volume >= 0.0):
        raise LiquidityError(
            f"captured_volume must be finite and non-negative, got {captured_volume}"
        )

    size = abs(position_size)
    if size == 0.0:
        # No position, nothing to exit.
        return PositionLiquidity(
            liquidity_version=config.version,
            contract_key=contract_key,
            position_size=position_size,
            captured_volume=captured_volume,
            participation_rate=config.participation_rate,
            max_exit_sessions=config.max_exit_sessions,
            exit_sessions=0.0,
            status=STATUS_OK,
        )

    sessions_capacity = config.participation_rate * captured_volume
    if sessions_capacity == 0.0:
        # A real, captured zero-volume session: the position cannot be exited at all.
        return PositionLiquidity(
            liquidity_version=config.version,
            contract_key=contract_key,
            position_size=position_size,
            captured_volume=captured_volume,
            participation_rate=config.participation_rate,
            max_exit_sessions=config.max_exit_sessions,
            exit_sessions=math.inf,
            status=STATUS_INEXITABLE,
        )

    exit_sessions = size / sessions_capacity
    status = (
        STATUS_INEXITABLE
        if exit_sessions > config.max_exit_sessions
        else STATUS_OK
    )
    return PositionLiquidity(
        liquidity_version=config.version,
        contract_key=contract_key,
        position_size=position_size,
        captured_volume=captured_volume,
        participation_rate=config.participation_rate,
        max_exit_sessions=config.max_exit_sessions,
        exit_sessions=exit_sessions,
        status=status,
    )


@dataclass(frozen=True, slots=True)
class LiquidityScreenInput:
    contract_key: str
    position_size: float
    captured_volume: float | None


@dataclass(frozen=True, slots=True)
class LiquidityReport:
    """Exit-difficulty screen over a book.

    ``inexitable`` lists the contracts that cannot be exited inside the bound;
    ``unknown_volume`` lists the contracts with no captured volume to screen against —
    a *coverage gap*, surfaced rather than buried.
    """

    liquidity_version: str
    screened: int
    inexitable: tuple[PositionLiquidity, ...]
    unknown_volume: tuple[PositionLiquidity, ...]
    lines: tuple[PositionLiquidity, ...]


def liquidity_report(
    positions: Iterable[LiquidityScreenInput],
    *,
    config: LiquidityConfig,
) -> LiquidityReport:
    lines = tuple(
        position_liquidity(
            contract_key=p.contract_key,
            position_size=p.position_size,
            captured_volume=p.captured_volume,
            config=config,
        )
        for p in positions
    )
    inexitable = tuple(line for line in lines if line.status == STATUS_INEXITABLE)
    unknown = tuple(line for line in lines if line.status == STATUS_UNKNOWN)
    return LiquidityReport(
        liquidity_version=config.version,
        screened=len(lines),
        inexitable=inexitable,
        unknown_volume=unknown,
        lines=lines,
    )


def screen_inputs(
    rows: Sequence[tuple[str, float, float | None]],
) -> tuple[LiquidityScreenInput, ...]:
    """Adapt ``(contract_key, position_size, captured_volume)`` rows to screen inputs."""
    return tuple(
        LiquidityScreenInput(contract_key=key, position_size=size, captured_volume=vol)
        for key, size, vol in rows
    )
