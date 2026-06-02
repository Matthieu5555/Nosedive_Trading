---
name: probe-environment
description: Run diagnostic checks at the start of a task before writing code against an unfamiliar library, API, dataset, or runtime. Use when the task depends on an external system whose behavior is not already verified in this session (library version, API contract, file schema, OS layout). Skip when the environment has already been probed in this conversation.
---

# Process

## 1. Name the unknowns

Before anything else, list what this task actually depends on that you have not yet verified in this session: specific library versions, function signatures you are about to call, API response shapes, dataset schemas, OS-specific paths, environment variables. If the list is empty, stop; no probing needed.

## 2. Probe each unknown with the smallest possible call

For each item:

- **Library version**: `uv run python -c "import X; print(X.__version__)"` or `uv pip show X`.
- **Function signature**: `uv run python -c "import inspect, X; print(inspect.signature(X.fn))"` or read the installed source.
- **External API**: one request with minimal payload; capture actual response shape, error modes, latency.
- **Dataset**: load a head, print dtypes, row count, null counts, a few representative samples. Never transform before inspecting.
- **Runtime**: `uv run python -c "import sys, platform; print(sys.version, platform.platform())"`; relevant env vars via `os.environ`.

Five lines of probing now prevents fifty lines of debugging later.

## 3. Reconcile with the plan

If a probe contradicts an assumption in the request or in your draft plan, stop and surface the mismatch before proceeding. Do not silently rewrite the plan around what you found; tell the user what changed and why.

## 4. Record what you learned

State the verified facts in one short block so the rest of the session can trust them: library X is version Y, function Z takes these args, the dataset has N rows with schema {...}. Do not repeat the probe later in the same session.

# Anti-patterns

Do not pattern-match from training data and call it verification. Do not assume docs match runtime. Do not skip probing because "it probably works the same as last time." Do not probe more than needed; each probe should answer a specific unknown.
