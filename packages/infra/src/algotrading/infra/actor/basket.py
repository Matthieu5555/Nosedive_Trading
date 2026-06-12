"""The per-index close basket the daily capture feeds into ``run_analytics`` (1A × 1B).

The live close path (``orchestration.eod_stages``) resolves one :class:`IndexBasket` per fired
index via its injected ``BasketSource`` (the 1C seam; ``infra_ibkr.live_capture`` supplies the
credentialed source) and runs the one pure ``run_analytics`` over it at that index's own session
close. This module owns only the basket shape and the default provider stamp — no capture loop
lives here.
"""

from __future__ import annotations

from dataclasses import dataclass

from algotrading.infra.contracts import (
    InstrumentKey,
    InstrumentMaster,
    Position,
    RawMarketEvent,
)


@dataclass(frozen=True, slots=True)
class IndexBasket:
    """The point-in-time basket to capture at close for one index (1A × 1B).

    ``instruments`` is the index plus its selected constituent contracts; ``events`` the
    close-session market observations for them (the raw quotes at the close); ``masters`` the
    matching instrument masters; ``positions`` any held positions to value at close (empty for
    a pure market-state capture). All caller-supplied per index — the close stage does not
    resolve the basket, it captures it. Determinism: the snapshot set is a pure function of
    these inputs and the injected close instant, so feeding the same close events twice (in any
    order) yields a byte-identical set.
    """

    instruments: tuple[InstrumentKey, ...]
    events: tuple[RawMarketEvent, ...]
    masters: tuple[InstrumentMaster, ...]
    positions: tuple[Position, ...] = ()


# The default source label the close grid's provider-partitioned cells are stamped with. The
# index registry's only provider sub-block today is `ibkr:` (ADR 0035), so the daily close set
# is captured off IBKR; a future Saxo/Deribit sibling passes its own label through `provider`.
DEFAULT_PROVIDER = "IBKR"
