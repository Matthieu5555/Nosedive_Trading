# Conventions — house style for this workspace

These are the standards code is expected to meet here. They are distilled from
the team's standing instructions and from the skills in `~/.claude/skills/`. Where
a skill goes deeper, this file names it rather than restating it, so the detailed
rules have exactly one home.

## Working discipline

- **Plan before coding anything non-trivial.** Restate the request, name the
  unknowns, draft numbered steps each with a concrete artifact and a way to
  verify it, then stop and get explicit confirmation before implementing. One
  step at a time; don't batch to look productive. See `plan-before-coding`.
- **Probe an unfamiliar environment before writing against it.** Verify library
  versions, function signatures, API response shapes, and dataset schemas with
  the smallest possible call before building on them. See `probe-environment`.
- **Run one full cycle for substantial work:** probe → understand existing code
  → plan → write failing tests → implement one step → verify → quality gate →
  update docs → (commit only if authorized). See `disciplined-coding-cycle`.
- **Scope discipline.** Every changed line traces to the request. No speculative
  abstraction, no cleanup of messes that aren't yours, no features beyond what
  was asked. If 200 lines could be 50, write 50.
- **Reuse before you write.** Grep for an existing function before adding a new
  one. No `foo_for_ingest` when `foo` already exists two files over.

## Python

`uv` for everything: `uv init`, `uv add`, `uv run`, `uv sync`. Never pip, poetry,
or conda. The backend targets Python 3.13.

- **Functional by default.** Pure functions, immutability, explicit data flow.
  OOP only for genuinely complex mutable state or when a system requires it.
- **Type hints on every argument and return.** `Optional[X]` only when `None` is
  a real, intended value.
- **One return type per function.** A function that returns a list returns an
  empty list when there's nothing, never `None`. No sentinel error values.
- **Structured data, not loose dicts.** `dataclass` (`frozen=True` when not
  mutated) or `TypedDict` for JSON-shaped payloads.
- **Naming.** Functions are verbs, full words, no abbreviations
  (`inverse_covariance_matrix`, not `inv_cov`). Files named for their contents.
  **No `utils.py`, `helpers.py`, `misc.py`, `common.py`.**
- **`pathlib.Path` throughout.** No string path concatenation, no `os.path.join`
  in new code.
- **Structured logging**, key-value fields, not f-strings jammed into a message.
  No `print` in library code.
- **Errors.** No bare `except:` / `except Exception:`. Custom exception classes
  that carry the value that triggered them. Define expected variants out of
  existence (deletion is idempotent, search returns empty) rather than raising.
- **Configuration** centralized in a validated config object. Hardcoded values
  sit at the top of the file with a comment on purpose and impact.
- **Dependency injection.** Functions accept their dependencies as parameters
  rather than constructing clients internally.

Before declaring Python work done, run it through `python-quality-gate`. For
design-sensitive modules (deep interfaces, orthogonality, information hiding),
run `review-module-depth`.

## Testing — non-negotiable

Code without tests is not presentable. See `write-tests`.

- Pick the lowest level that catches the bug class: unit for pure computation,
  integration for wiring, property-based for invariants, contract for API
  shapes, component/end-to-end for UI.
- **Derive expected values independently** — hand-calculated, from a reference,
  or from the spec. Never copy the output of the code under test. Cite where the
  expected value came from in a comment.
- Floats compared with explicit tolerances (`pytest.approx`,
  `numpy.testing.assert_allclose` with `rtol`/`atol`), never `==`.
- Parameterize with named cases. Cover edge cases (empty, single, boundary,
  NaN/inf, degenerate shapes). Strong assertions on full shape and value.
- Test behavior, not implementation. No arbitrary sleeps — wait for the real
  condition.
- Write the assertion, see it fail for the right reason, then implement.

## Financial / time-series code

No look-ahead bias, ever. See `check-lookahead-bias`.

- All data access goes through an as-of abstraction; every read is
  parameterizable by an as-of date.
- Fundamental data keyed on **publication/availability date**, not period end.
  Use the data vintage known at the as-of date, not restated finals.
- No global normalization before the train/test split. Time-ordered,
  walk-forward, date-based splits. Touch the test set once.
- Log every variant tested, including failures. Past five trials, apply a
  multiple-testing correction.
- Every number in a report traces to a line of code; every figure is
  script-generated. The pipeline runs from one command on a fresh environment.

## Documentation

When user-facing behavior, setup, config, commands, data flow, or limitations
change, update the docs in the same change. READMEs lead with a TL;DR and the
fastest run path, and document every config field explicitly. Describe what the
code actually does, never what it's supposed to do. See `write-readme`.

## Checking factual claims

When accuracy matters (reports, analyses, plans with many factual statements),
audit the claims before finalizing. See `claim-audit`.
