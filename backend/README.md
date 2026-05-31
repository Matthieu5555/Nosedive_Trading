# backend

Python service and quant logic for the workspace. Uses `uv`, targets Python 3.13.

## TL;DR

This is currently a **skeleton**. `main.py` is a hello-world stub — there is no
FastAPI `app` object yet, despite the workspace README's example `uvicorn` command.
Declared dependencies are `fastapi`, `uvicorn`, `numpy`, `pandas`, and `polars`.
The first real work is to stand up the actual service and wire a test/lint gate.

## Run

```
cd backend
uv sync
uv run python main.py     # prints "Hello from backend!"
```

Once a FastAPI app exists (e.g. `app` in `main.py`), the dev server will be:

```
uv run uvicorn main:app --reload
```

That command does **not** work yet — there is no `app` to serve. Update this
section when the app object lands.

## Verify

```
uv run pytest -q
```

Not wired yet: `pytest`, `ruff`, and `mypy` are not in `pyproject.toml`. Adding
them (`uv add --dev pytest ruff mypy`) and writing the first tests is the first
quality task. The intended full gate is
`uv run ruff check . && uv run mypy . && uv run pytest -q`.

## Conventions

Follows `/srv/project/.agent/conventions.md` — functional by default, type hints
everywhere, `pathlib`, structured logging, no `utils.py`. Quant/time-series code
must obey the look-ahead rules in that file. Keep this README current when you
change what the backend does.
