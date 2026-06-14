"""Resolve an abstract grid-cell order-ticket leg into a concrete, priced paper fill.

This is the seam WS 3A deliberately deferred (its ``# 3B:`` marker) and
[[execution-booking-commit]] (TARGET §7 #1) assumes is already done: the transform from
**grid-cell space** — a :class:`~algotrading.infra.orders.TicketLeg` naming
``(underlying, tenor_label, delta_band)`` — into **concrete space**, a real
``(strike, expiry, right)`` contract carrying a price. It is ruled by
[ADR 0043](../../../../../.agent/decisions/0043-fills-are-concrete-contracts-resolved-at-booking.md):
*a booked fill is a concrete contract, resolved at booking time*.

Design (per ADR 0043 and the task spec):

* **Pure + as-of.** :func:`concretize` is a pure function. It performs no I/O, reads no wall
  clock, touches no broker and reads no credential. Every input — the captured chain and the
  marks — is passed in as a value, keyed by the booking ``as_of`` date. An as-of replay of an
  old date resolves *that* date's chain, never today's (the look-ahead guard): the chain is the
  only source of strikes/expiries, so a stale chain can only yield stale contracts.
* **Resolution.** The grid cell is matched to the WS-1F :class:`ProjectedOptionAnalytics` row
  for ``(underlying, tenor_label, delta_band)`` — the *same* join key the risk engine uses
  (:func:`algotrading.infra.risk.multileg.analytics_cell_key`), never a parallel one. That row
  already carries the solved ``strike``, the signed ``target_delta`` and the ``maturity_years``;
  the option ``right`` comes from the band by the canonical rule
  (:func:`option_right_for_band`, the public twin of the projection's private mapping). The
  ``(strike, right, expiry)`` is then bound to a real listed contract off the captured chain so
  the booked book holds a true ``InstrumentKey`` / ``contract_key``, identical to the key a live
  broker-send would bind (ADR 0043: no re-keying at the live boundary).
* **Paper mark.** The fill books at the mid of the as-of :class:`MarketStateSnapshot` for the
  resolved contract (``(bid + ask) / 2``) when a finite two-sided quote exists; otherwise it
  falls back to the analytics row's model ``price`` — a stated, deterministic rule, never a
  wall-clock read. The chosen rule is recorded on the fill (:attr:`ConcreteFill.mark_source`).

Every unresolvable cell is a labelled :class:`ConcretizationError` carrying the offending grid
coordinate and a machine-readable reason — never a silent default, never a bare exception.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.contracts import (
    InstrumentKey,
    MarketStateSnapshot,
    ProjectedOptionAnalytics,
)
from algotrading.infra.orders import Side, TicketLeg
from algotrading.infra.risk.multileg import CellKey, analytics_cell_key

# Float comparisons against the captured chain use a relative+absolute band, never ``==``: a
# strike read back from a Parquet round-trip can differ in the last ulp. These are tight enough
# that two genuinely different listed strikes never collide (index strikes are spaced by points).
_STRIKE_REL_TOL = 1e-9
_STRIKE_ABS_TOL = 1e-6

# The mark rule labels recorded on a fill, so a reader knows *how* the paper price was set.
MARK_SOURCE_SNAPSHOT_MID = "snapshot_mid"
MARK_SOURCE_ANALYTICS_PRICE = "analytics_model_price"


class ConcretizationError(Exception):
    """A labelled rejection: a grid cell could not be resolved to a concrete, priced contract.

    Carries the offending grid ``cell`` coordinate and a machine-readable ``reason`` so the
    caller (booking-commit, then the BFF) can surface *which* cell failed and *why* — never an
    opaque ``KeyError`` and never a silent default. ``reason`` is one of:

    * ``"not_an_option_leg"`` — concretization is for option legs; a stock leg has no grid cell.
    * ``"no_analytics_row"`` — no WS-1F analytics cell for the coordinate in the as-of chain.
    * ``"provider_ambiguous"`` — the cell is seeded by more than one provider in the read scope.
    * ``"no_listed_contract"`` — the resolved ``(strike, right)`` matches no listed contract.
    * ``"strike_ambiguous"`` — more than one listed expiry ties for the cell's maturity.
    * ``"no_mark"`` — neither a finite snapshot mid nor a finite analytics price is available.
    """

    def __init__(self, reason: str, *, cell: CellKey) -> None:
        self.reason = reason
        self.cell = cell
        super().__init__(f"cell={cell!r}: {reason}")


@dataclass(frozen=True, slots=True)
class ConcreteFill:
    """A concrete, priced paper fill — the seam booking-commit synthesizes a position from.

    This is the single shared shape co-designed with [[execution-booking-commit]] (which writes
    the fill record and the audit row) and [[execution-fills-position-store]] (which keys a
    :class:`~algotrading.infra.contracts.Position` by ``contract_key``). One source, not three:
    a renamed field here breaks the seam round-trip test loudly.

    * ``contract_key`` — the canonical instrument-key string (``instrument.canonical()``), the
      exact key the fills store and risk/attribution read by.
    * ``instrument`` — the resolved concrete :class:`InstrumentKey`
      (``underlying, strike, expiry, right`` + the broker identity off the captured chain).
    * ``underlying`` / ``side`` / ``quantity`` — carried straight from the ticket leg
      (``quantity`` is the positive magnitude; ``side`` carries the direction).
    * ``fill_price`` — the paper mark the fill books at, in the contract's quote currency.
    * ``mark_source`` — :data:`MARK_SOURCE_SNAPSHOT_MID` or
      :data:`MARK_SOURCE_ANALYTICS_PRICE`, recording which rule set ``fill_price``.
    * ``as_of`` — the booking date the resolution was performed as-of (provenance for replay).
    * ``tenor_label`` / ``delta_band`` — the originating grid coordinate, kept so a booked
      concrete fill traces back to the planning intention that created it.
    """

    contract_key: str
    instrument: InstrumentKey
    underlying: str
    side: Side
    quantity: float
    fill_price: float
    mark_source: str
    as_of: date
    tenor_label: str
    delta_band: str


def option_right_for_band(delta_band: str, target_delta: float) -> str:
    """The option right (``"C"``/``"P"``) for a delta-band cell — the public twin of the rule
    in :func:`algotrading.infra.surfaces.projection._option_right_for_band`.

    The label **suffix** governs (``…p`` → put, ``…c`` → call); a label with no side suffix
    (the bare ``"atm"`` pillar) falls back to the signed target (negative → put, else call).
    This lets the ATM call (``atm``) and the ATM put (``atmp``), both at target ``0.0`` and the
    one ATM-forward strike, be a call and a put — the two legs of a straddle. A test pins this
    against the projection's private authority so the two can never drift apart.
    """
    if delta_band.endswith("p"):
        return "P"
    if delta_band.endswith("c"):
        return "C"
    return "P" if target_delta < 0.0 else "C"


@dataclass(frozen=True, slots=True)
class ConcreteChain:
    """The captured chain + marks read as-of one booking date — the resolver's only data source.

    Bundling the three reads behind one as-of value keeps :func:`concretize` pure and makes the
    look-ahead guard structural: there is no other place a strike, expiry, or price can come
    from, so an old-date chain can only yield that date's contracts.

    * ``analytics_rows`` — the WS-1F :class:`ProjectedOptionAnalytics` grid for the booking
      ``(trade_date, underlying)``; each row resolves one grid cell to a solved
      ``(strike, target_delta, maturity_years)`` + a model ``price``.
    * ``listed_contracts`` — the concrete option :class:`InstrumentKey`s listed as-of the
      booking date (``InstrumentMaster.instrument`` rows): the source of the real
      ``(strike, expiry, right)`` + broker identity a fill binds.
    * ``snapshot_by_contract_key`` — the as-of :class:`MarketStateSnapshot` per
      ``contract_key``, the mid source for the paper mark.
    """

    analytics_rows: tuple[ProjectedOptionAnalytics, ...]
    listed_contracts: tuple[InstrumentKey, ...]
    snapshot_by_contract_key: Mapping[str, MarketStateSnapshot]

    @classmethod
    def build(
        cls,
        *,
        analytics_rows: Iterable[ProjectedOptionAnalytics],
        listed_contracts: Iterable[InstrumentKey],
        snapshots: Iterable[MarketStateSnapshot],
    ) -> ConcreteChain:
        """Materialize a chain from iterables, indexing snapshots by ``contract_key``."""
        return cls(
            analytics_rows=tuple(analytics_rows),
            listed_contracts=tuple(listed_contracts),
            snapshot_by_contract_key={
                snapshot.instrument_key: snapshot for snapshot in snapshots
            },
        )


def _analytics_cell_index(
    rows: Sequence[ProjectedOptionAnalytics],
) -> tuple[dict[CellKey, ProjectedOptionAnalytics], set[CellKey]]:
    """Index analytics rows by grid cell, flagging cells seeded by more than one provider.

    Mirrors :func:`algotrading.infra.risk.multileg._index_rows_by_cell`: the grid is
    provider-partitioned, so a cross-provider read can carry two rows for one cell; picking one
    silently would be a hidden, non-deterministic choice, so an ambiguous cell becomes a labelled
    failure rather than an arbitrary pick.
    """
    by_cell: dict[CellKey, ProjectedOptionAnalytics] = {}
    ambiguous: set[CellKey] = set()
    for row in rows:
        key = analytics_cell_key(row.underlying, row.tenor_label, row.delta_band)
        if key in by_cell and by_cell[key].provider != row.provider:
            ambiguous.add(key)
        by_cell[key] = row
    return by_cell, ambiguous


def _strikes_match(listed: float, target: float) -> bool:
    """Whether a listed strike equals the cell's solved strike within the float band."""
    return math.isclose(listed, target, rel_tol=_STRIKE_REL_TOL, abs_tol=_STRIKE_ABS_TOL)


def _resolve_listed_contract(
    *,
    underlying: str,
    strike: float,
    right: str,
    as_of: date,
    listed_contracts: Sequence[InstrumentKey],
    cell: CellKey,
) -> InstrumentKey:
    """Bind the cell's ``(strike, right)`` to one real listed contract off the captured chain.

    A candidate is a listed *option* whose ``underlying_symbol``/``option_right`` match and whose
    ``strike`` matches the cell's solved strike within the float band. Among candidates the one
    whose ``expiry`` is **soonest on/after ``as_of``** wins (the front listed expiry for that
    strike — the contract a desk would actually book), deterministically. No on/after candidate
    (only already-expired listings) or no candidate at all is :class:`ConcretizationError`
    ``"no_listed_contract"``; an exact tie on the chosen expiry is ``"strike_ambiguous"``.
    """
    candidates = [
        contract
        for contract in listed_contracts
        if contract.is_option()
        and contract.underlying_symbol == underlying
        and contract.option_right == right
        and contract.strike is not None
        and _strikes_match(contract.strike, strike)
    ]
    live = [c for c in candidates if c.expiry is not None and c.expiry >= as_of]
    if not live:
        raise ConcretizationError("no_listed_contract", cell=cell)
    soonest_expiry = min(c.expiry for c in live if c.expiry is not None)
    on_soonest = [c for c in live if c.expiry == soonest_expiry]
    if len(on_soonest) > 1:
        raise ConcretizationError("strike_ambiguous", cell=cell)
    return on_soonest[0]


def _paper_mark(
    *,
    contract_key: str,
    analytics_price: float,
    snapshot_by_contract_key: Mapping[str, MarketStateSnapshot],
    cell: CellKey,
) -> tuple[float, str]:
    """The paper fill price + the label of the rule that set it (ADR 0043's stated rule).

    Mid of the as-of snapshot (``(bid + ask) / 2``) when a finite two-sided positive quote
    exists; otherwise the analytics model ``price`` when finite and positive. Neither available
    is :class:`ConcretizationError` ``"no_mark"`` — never a silent zero.
    """
    snapshot = snapshot_by_contract_key.get(contract_key)
    if snapshot is not None:
        mid = (snapshot.bid + snapshot.ask) / 2.0
        if math.isfinite(mid) and mid > 0.0:
            return mid, MARK_SOURCE_SNAPSHOT_MID
    if math.isfinite(analytics_price) and analytics_price > 0.0:
        return analytics_price, MARK_SOURCE_ANALYTICS_PRICE
    raise ConcretizationError("no_mark", cell=cell)


def concretize(
    ticket_leg: TicketLeg,
    *,
    as_of: date,
    chain: ConcreteChain,
) -> ConcreteFill:
    """Resolve one grid-cell option ticket leg into a concrete, priced :class:`ConcreteFill`.

    **Pure + as-of:** no I/O, no clock, no broker, no credential. ``chain`` is the captured
    chain + marks as-of ``as_of`` (see :class:`ConcreteChain`); every strike, expiry and price
    comes from it, so an old-date replay resolves that date's contract, never today's.

    Steps: match the leg's grid cell to its WS-1F analytics row (the same join key the risk
    engine uses) → read the solved ``strike`` and the ``right`` from the band → bind a real
    listed contract off the chain → mark it by the paper rule. Every unresolvable step is a
    labelled :class:`ConcretizationError` carrying the offending cell, never a silent default.
    """
    cell = analytics_cell_key(
        ticket_leg.underlying, ticket_leg.tenor_label, ticket_leg.delta_band
    )
    if ticket_leg.instrument_kind != "option":
        raise ConcretizationError("not_an_option_leg", cell=cell)

    by_cell, ambiguous = _analytics_cell_index(chain.analytics_rows)
    if cell in ambiguous:
        raise ConcretizationError("provider_ambiguous", cell=cell)
    row = by_cell.get(cell)
    if row is None:
        raise ConcretizationError("no_analytics_row", cell=cell)

    right = option_right_for_band(row.delta_band, row.target_delta)
    instrument = _resolve_listed_contract(
        underlying=ticket_leg.underlying,
        strike=row.strike,
        right=right,
        as_of=as_of,
        listed_contracts=chain.listed_contracts,
        cell=cell,
    )
    contract_key = instrument.canonical()
    fill_price, mark_source = _paper_mark(
        contract_key=contract_key,
        analytics_price=row.price,
        snapshot_by_contract_key=chain.snapshot_by_contract_key,
        cell=cell,
    )

    return ConcreteFill(
        contract_key=contract_key,
        instrument=instrument,
        underlying=ticket_leg.underlying,
        side=ticket_leg.side,
        quantity=ticket_leg.quantity,
        fill_price=fill_price,
        mark_source=mark_source,
        as_of=as_of,
        # An option leg always carries its grid cell (the TicketLeg contract enforces it); the
        # narrowing tells the type checker the seam fields are non-None strings.
        tenor_label=ticket_leg.tenor_label or "",
        delta_band=ticket_leg.delta_band or "",
    )
