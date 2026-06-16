from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_TEST_MODULE = Path(__file__).parents[1] / "test_account_reconciliation_golden.py"
_GOLDEN = Path(__file__).parent / "account_reconciliation.json"


def _load_test_module() -> object:
    spec = importlib.util.spec_from_file_location("recon_golden_seed", _TEST_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_TEST_MODULE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def regenerate() -> None:
    module = _load_test_module()
    payload = module.golden_payload()  # type: ignore[attr-defined]
    _GOLDEN.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    regenerate()
