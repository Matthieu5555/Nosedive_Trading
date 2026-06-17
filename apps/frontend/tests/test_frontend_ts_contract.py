"""Cross-language contract: the hand-written TS interfaces mirror the BFF Python serializers.

The Python serializers are tested (test_onglet*_contracts.py assert dict keys). The TS side casts
with `as T` and so silently tolerates a renamed/dropped key. The strongest catch is on the TS side
(typed JSON fixtures checked by tsc; see web/src/api.contract.test.ts). This module closes the loop
from the PYTHON side for the one seam the TS fixture cannot capture by HTTP in an offline/partial
build: the IBKR session-state payload (`/api/ibkr/status` is not mounted on every BFF build and is
unreachable via the offline proxy).

It does two things:

  1. Asserts the live `_status_payload()` key set EXACTLY equals the keys the TS `IbkrStatus`
     interface declares (parsed out of web/src/api.ts). A rename/drop on EITHER side breaks this:
     drop a key in the serializer -> Python set shrinks; rename a key in api.ts -> TS set shifts.

  2. Regenerates web/src/__fixtures__/contracts/ibkr_status.json from the live payload, so the
     fixture the TS compile-time pin checks against stays honest (a stale fixture that passes is
     worthless). The TS test then type-checks that fixture against `IbkrStatus`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from algotrading.frontend.routers.ibkr import _status_payload

_REPO = Path(__file__).resolve().parents[3]
_API_TS = _REPO / "apps/frontend/web/src/api.ts"
_FIXTURE = _REPO / "apps/frontend/web/src/__fixtures__/contracts/ibkr_status.json"


def _ts_interface_keys(source: str, name: str) -> set[str]:
    """Parse the top-level field names of `export interface <name> { ... }` from a .ts source.

    Deliberately tiny: matches the interface body to its first closing brace and pulls each
    `key:` / `key?:` at the start of a line. The IbkrStatus body is flat (no nested objects), so
    this is exact for this seam.
    """
    m = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.DOTALL)
    assert m, f"could not find `export interface {name}` in {_API_TS}"
    body = m.group(1)
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\??:", body, re.MULTILINE))


def test_ibkr_status_keys_match_ts_interface() -> None:
    payload = _status_payload()
    ts_keys = _ts_interface_keys(_API_TS.read_text(), "IbkrStatus")

    assert set(payload) == ts_keys, (
        "IBKR status contract drift between the BFF serializer "
        "(routers/ibkr.py::_status_payload) and the TS IbkrStatus interface (web/src/api.ts). "
        f"Python keys: {sorted(payload)}; TS keys: {sorted(ts_keys)}. "
        f"Only in Python: {sorted(set(payload) - ts_keys)}; "
        f"only in TS: {sorted(ts_keys - set(payload))}."
    )


def test_ibkr_status_value_types_are_honest() -> None:
    payload = _status_payload()
    for k in ("configured", "authenticated", "established", "competing"):
        assert isinstance(payload[k], bool), f"IbkrStatus.{k} must serialize as a bool"
    assert payload["account"] is None or isinstance(payload["account"], str)
    assert isinstance(payload["detail"], str) and payload["detail"], (
        "IbkrStatus.detail must always carry the next-operator-step line in plain language"
    )


def test_regenerate_ibkr_status_fixture_stays_honest() -> None:
    """Refresh the TS fixture from the live payload so the compile-time pin checks current reality.

    Writing in a test keeps the fixture honest without a separate manual step: every CI run that
    touches this module re-captures the real shape. The keys are asserted above; here we only
    persist, and verify the on-disk fixture round-trips to the same key set.
    """
    payload = _status_payload()
    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    _FIXTURE.write_text(json.dumps(payload, indent=2) + "\n")

    on_disk = json.loads(_FIXTURE.read_text())
    assert set(on_disk) == set(payload)
