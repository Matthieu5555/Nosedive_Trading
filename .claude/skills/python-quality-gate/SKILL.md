---
name: python-quality-gate
description: Pre-flight checklist to run against Python code before declaring a task done. Use after implementing a Python module, before committing, or when the user asks for a quality pass. Produces a list of violations with file and line references and short suggested fixes.
---

# Process

Run each check in order. For each violation, record `path:line` and a one-line fix. Do not auto-patch without confirmation.

## 1. Type hints

Every function argument and return value has a type hint. No exceptions. `Optional[X]` used explicitly when `None` is a legitimate return, not implied.

## 2. Return type consistency

Each function returns one type. A function that returns a list always returns a list (empty when there are no results), never sometimes `None`. No sentinel error values; raise typed exceptions instead.

## 3. Structured data

`dataclass` (or `TypedDict` for JSON-shaped payloads) wherever a dict is passed between functions as structured data. Prefer `frozen=True` when the object is not mutated after creation.

## 4. Naming

- Functions are verbs (`calculate_`, `load_`, `return_`). No abbreviations. `inverse_covariance_matrix`, not `inv_cov`.
- Files named for what they contain. No `utils.py`, `helpers.py`, `misc.py`, `common.py`.
- Variables describe the domain concept, not the type.

## 5. File paths

`pathlib.Path` throughout. No string concatenation for paths, no `os.path.join` in new code.

## 6. Logging

No `print` statements in library code. Structured logging with key-value fields (`logger.info("event", user_id=..., duration_ms=...)`), not f-strings jammed into message text.

## 7. Configuration

Hardcoded values at the top of the file with a comment stating purpose and what changing them affects. Secrets and environment-specific values read from a validated config object, not scattered `os.environ.get` calls.

## 8. Error handling

No bare `except:` or `except Exception:`. Custom exception classes carry context (the value that triggered the error, not just a message). Expected variants (missing optional key, already-deleted target, empty search) handled in normal flow, not exceptions.

## 9. Function shape

Each function does one thing at one level of abstraction. High-level functions compose lower-level calls; they do not mix orchestration with detailed computation. Flag any function over ~40 lines or with more than one level of nested control flow as a candidate for decomposition, but do not decompose automatically; shallow helpers are worse than a slightly long but coherent function.

## 10. Immutability

Tuples, frozensets, and `frozen=True` dataclasses where the data does not change. Comprehensions and generators preferred over `append`-in-loop. Generators when the collection is large or streamed.

## 11. Dependency injection

Functions accept their dependencies as parameters rather than constructing them inside. If a function calls `SomeClient()` internally, flag it; the client should be passed in.

## 12. Tests exist

A corresponding test file exists and covers the public interface. If the task added new behavior and no test was written, this is a blocking finding. (See `write-tests` for what the tests should look like.)

# Output format

Group findings by file. For each: `path:line — <issue> → <fix>`. At the end, list any findings that require user judgment (redesigns rather than mechanical fixes) separately.
