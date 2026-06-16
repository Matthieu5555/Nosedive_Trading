from __future__ import annotations

import hashlib

_ID_SEPARATOR = "\x1f"


def content_event_id(instrument_key: str, field_name: str, sequence: int) -> str:
    payload = _ID_SEPARATOR.join((instrument_key, field_name, str(sequence)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
