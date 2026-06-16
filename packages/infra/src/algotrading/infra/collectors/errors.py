from __future__ import annotations


class CollectorError(Exception):
    pass


class ReservedFieldError(CollectorError):

    def __init__(self, field_name: str) -> None:
        self.field_name = field_name
        super().__init__(
            f"field name {field_name!r} uses the reserved '__' meta-event namespace"
        )
