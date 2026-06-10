"""WS 1A — membership *sources*: turn a third-party constituent feed into typed changes.

Index membership is point-in-time **reference data** (who was in the index, with dated
add/remove), not a market-data quote — and it is **not available from the IBKR API** (verified
against the official IBKR docs: the contract/scanner endpoints cannot enumerate an index's
members; IBKR's own quant blog sources constituents from third parties). So membership is pulled
from a constituent source and the dated history is kept by us (the ``index_constituents``
bitemporal table), then IBKR is queried per resolved name for chains/bars (1B/1C).

This module is only the **reader**: it maps a raw vendor CSV into typed
:class:`~algotrading.infra.universe.membership.MembershipChange` records. The typed contract,
validation, bitemporal append-only write and the as-of resolver already live in
``membership.py`` — a source here never touches storage, it only parses. Yahoo / ``yfinance`` is
deliberately **not** a source: the owner mandate excludes Yahoo as unreliable (OQ-2) and
``yfinance`` exposes no constituents feed anyway.

Parsing (:func:`parse_constituents_csv`) is a pure function — unit-tested offline against a
committed CSV sample, no network. The concrete sources add only the HTTP fetch around it.
"""

from __future__ import annotations

import csv
import io
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from .membership import MembershipChange

# S&P 500 inception — the honest default ``effective_add_date`` for a current-snapshot row whose
# source carries no per-name add date (the name is a member now; we do not claim to know since
# when). A source that *does* carry add dates (e.g. the "Date added" column) overrides this.
_SP500_INCEPTION = date(1957, 3, 4)

_HTTP_TIMEOUT_S = 20.0
_USER_AGENT = "algotrading-membership-ingest/1.0"


class MembershipSource(Protocol):
    """A pull source for an index's constituents, as typed dated changes.

    ``knowledge_date`` is the date the pull is attributed to (the knowledge axis); it is passed
    in, never read from a wall clock here, so a backfill/replay is deterministic.
    """

    def fetch(self, index: str, knowledge_date: date) -> list[MembershipChange]: ...


def parse_constituents_csv(
    text: str,
    *,
    index: str,
    vendor: str,
    knowledge_date: date,
    default_add_date: date,
    symbol_field: str = "Symbol",
    add_date_field: str | None = None,
    weight_field: str | None = None,
) -> list[MembershipChange]:
    """Parse a constituent-snapshot CSV into typed :class:`MembershipChange` rows.

    One row per current member, with ``effective_remove_date=None`` (a current snapshot states
    who is in *now*, not who left). ``effective_add_date`` is read from ``add_date_field`` when
    the source provides it (a real, honest add date) and falls back to ``default_add_date``
    otherwise. ``weight`` is read from ``weight_field`` when present and is ``None`` (labeled
    unavailable) otherwise — never zeroed or equal-weighted (OQ-1/OQ-3). A malformed date or
    weight cell degrades to the fallback / ``None`` rather than dropping the name.

    Pure: no network, no clock, no storage — the caller owns ``knowledge_date`` and the write.
    """
    reader = csv.DictReader(io.StringIO(text))
    changes: list[MembershipChange] = []
    seen: set[str] = set()
    for row in reader:
        symbol = (row.get(symbol_field) or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)

        add_date = default_add_date
        if add_date_field:
            raw_add = (row.get(add_date_field) or "").strip()
            if raw_add:
                try:
                    add_date = date.fromisoformat(raw_add[:10])
                except ValueError:
                    add_date = default_add_date

        weight: float | None = None
        if weight_field:
            raw_weight = (row.get(weight_field) or "").strip().rstrip("%")
            if raw_weight:
                try:
                    weight = float(raw_weight)
                except ValueError:
                    weight = None

        changes.append(
            MembershipChange(
                index=index,
                constituent=symbol,
                effective_add_date=add_date,
                effective_remove_date=None,
                knowledge_date=knowledge_date,
                vendor=vendor,
                weight=weight,
            )
        )
    return changes


def _http_get(url: str) -> str:
    """GET a URL's body as text (stdlib only — the project pins no general HTTP client here)."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:  # noqa: S310
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset)


@dataclass(frozen=True, slots=True)
class SP500DatasetsSource:
    """Free, dated **S&P 500** membership from the Frictionless ``datasets/s-and-p-500-companies``
    repo (the source IBKR's own quant blog points to). Its ``Date added`` column gives a real
    per-name ``effective_add_date``; weights are absent (``None``). It is a **current** snapshot,
    so it carries no removals — a far-past as-of is survivorship-biased. Full dated history (real
    removals) is the monthly-series / Siblis upgrade (OQ-3), behind this same protocol.
    """

    url: str = (
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
    )
    vendor: str = "github:datasets/s-and-p-500-companies"

    def fetch(self, index: str, knowledge_date: date) -> list[MembershipChange]:
        text = _http_get(self.url)
        return parse_constituents_csv(
            text,
            index=index,
            vendor=self.vendor,
            knowledge_date=knowledge_date,
            default_add_date=_SP500_INCEPTION,
            symbol_field="Symbol",
            add_date_field="Date added",
        )


@dataclass(frozen=True, slots=True)
class YfiuaSnapshotSource:
    """Free current snapshot from ``yfiua/index-constituents`` (Yahoo-consistent symbols), for
    indices the datasets repo does not cover. The root CSV is ``Symbol,Name`` with no dates, so
    every name gets ``default_add_date`` (we attest membership as of ``knowledge_date``, not a
    real history) — use the monthly ``$YYYY/$MM`` series or Siblis for true dated history (OQ-3).
    """

    code: str
    default_add_date: date
    vendor: str = "github:yfiua/index-constituents"

    @property
    def url(self) -> str:
        return f"https://yfiua.github.io/index-constituents/constituents-{self.code}.csv"

    def fetch(self, index: str, knowledge_date: date) -> list[MembershipChange]:
        text = _http_get(self.url)
        return parse_constituents_csv(
            text,
            index=index,
            vendor=self.vendor,
            knowledge_date=knowledge_date,
            default_add_date=self.default_add_date,
            symbol_field="Symbol",
        )


@dataclass(frozen=True, slots=True)
class CsvFileSource:
    """A committed **local CSV** of an index's current members, with an optional weight column.

    The honest seam for an index whose free feed carries no weights (the MVP gap): point this at a
    CSV that has a ``Weight`` column and the weights flow unchanged through the same parser →
    contract → resolver → BFF → front as any vendor feed. No network, no clock — the file is read
    from disk and parsed by the same pure :func:`parse_constituents_csv`. A blank/absent weight
    cell stays ``None`` (labeled unavailable), never zeroed or equal-weighted (OQ-1/OQ-3), so a
    partially-weighted file is honest rather than silently wrong.

    ``vendor`` records the file's provenance (e.g. an ETF-holdings snapshot + its date) so the
    bitemporal table keeps an audit trail of where the weights came from.
    """

    path: Path
    vendor: str
    default_add_date: date
    symbol_field: str = "Symbol"
    add_date_field: str | None = None
    weight_field: str | None = "Weight"

    def fetch(self, index: str, knowledge_date: date) -> list[MembershipChange]:
        text = Path(self.path).read_text(encoding="utf-8")
        return parse_constituents_csv(
            text,
            index=index,
            vendor=self.vendor,
            knowledge_date=knowledge_date,
            default_add_date=self.default_add_date,
            symbol_field=self.symbol_field,
            add_date_field=self.add_date_field,
            weight_field=self.weight_field,
        )
