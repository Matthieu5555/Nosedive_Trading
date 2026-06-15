"""Export the BFF's OpenAPI schema to a committed JSON artifact for TS codegen.

The frontend's typed client (``apps/frontend/web/src/api/schema.d.ts``) is generated from
this file by ``openapi-typescript`` (the web ``gen:api`` script). Committing the schema and
diffing it in CI (``just web-contract`` / the ``web-contract`` gate job) makes a backend
contract change that is not regenerated fail the build — that is the drift guard.

The app is built over an injected :class:`~algotrading.frontend.context.AppContext` pointed
at a throwaway scratch store, never the canonical ``data/``: schema export only introspects
the route table, so no parquet rows are read, but the injected context keeps it that way by
construction (it also avoids the repo-root / registry walk ``AppContext.build`` would do).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.infra.storage import ParquetStore

# Written under the web app so the ``gen:api`` codegen and the drift diff both find it there.
_OUTPUT = (
    Path(__file__).resolve().parents[1] / "apps" / "frontend" / "web" / "openapi.json"
)


def _scratch_context(root: Path) -> AppContext:
    """An ``AppContext`` over an empty scratch store — never the canonical ``data/``."""
    return AppContext(
        store_root=root,
        configs_dir=root / "configs",
        store=ParquetStore(root),
        default_underlying="SX5E",
    )


def export_openapi(output: Path = _OUTPUT) -> Path:
    """Build the app over a scratch context and write ``app.openapi()`` to ``output``."""
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(_scratch_context(Path(tmp) / "data"))
        schema = app.openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Trailing newline + sorted keys so the diff is stable regardless of dict ordering.
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> None:
    """CLI entrypoint: export the schema and report where it landed."""
    path = export_openapi()
    print(f"wrote OpenAPI schema to {path}")


if __name__ == "__main__":
    main()
