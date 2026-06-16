from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from .profiles import ProfileVersion
from .runs import RunRecord


@runtime_checkable
class RunRepository(Protocol):

    def record(self, run: RunRecord) -> None:
        ...

    def list_runs(self, job: str) -> tuple[RunRecord, ...]:
        ...

    def last_healthy(self, job: str) -> RunRecord | None:
        ...


@runtime_checkable
class ProfileRepository(Protocol):

    def save(self, version: ProfileVersion) -> None:
        ...

    def resolve_as_of(self, name: str, on_date: date) -> ProfileVersion | None:
        ...

    def get(self, name: str, content_hash: str) -> ProfileVersion | None:
        ...

    def versions(self, name: str) -> tuple[ProfileVersion, ...]:
        ...
