from __future__ import annotations

import csv
import io
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from .membership import MembershipChange

_SP500_INCEPTION = date(1957, 3, 4)

_HTTP_TIMEOUT_S = 20.0
_USER_AGENT = "algotrading-membership-ingest/1.0"


class MembershipSource(Protocol):

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
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:  # noqa: S310
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset)


@dataclass(frozen=True, slots=True)
class SP500DatasetsSource:

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
