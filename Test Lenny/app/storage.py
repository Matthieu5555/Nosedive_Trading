from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any


class LocalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                create table if not exists raw_events (
                    id integer primary key autoincrement,
                    created_at text not null,
                    event_type text not null,
                    payload text not null
                )
                """
            )
            connection.execute(
                """
                create table if not exists order_log (
                    id integer primary key autoincrement,
                    created_at text not null,
                    status text not null,
                    payload text not null
                )
                """
            )

    def append_event(self, created_at: str, event_type: str, payload: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                "insert into raw_events(created_at, event_type, payload) values (?, ?, ?)",
                (created_at, event_type, json.dumps(to_jsonable(payload), sort_keys=True)),
            )

    def append_order(self, created_at: str, status: str, payload: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                "insert into order_log(created_at, status, payload) values (?, ?, ?)",
                (created_at, status, json.dumps(to_jsonable(payload), sort_keys=True)),
            )

    def recent_orders(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "select created_at, status, payload from order_log order by id desc limit ?",
                (limit,),
            ).fetchall()
        return [
            {"createdAt": created_at, "status": status, "payload": json.loads(payload)}
            for created_at, status, payload in rows
        ]

    def raw_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("select count(*) from raw_events").fetchone()
        return int(row[0])


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value
