---
name: review-module-depth
description: Review a module, class, or set of functions for interface depth, orthogonality, and information hiding. Use when finishing an implementation before declaring it done, when a design feels off, or when the user asks for a design review. Produces a list of specific issues with suggested restructurings.
---

# Process

## 1. Map the interface

List every public entry point of the module (functions, methods, exported classes). For each, write down its signature and one sentence on what it does. If you cannot summarize a function in one sentence, that is the first finding.

## 2. Check each interface for depth

For each public entry point, ask:

- **Functionality per unit of interface surface**: does this function do meaningful work, or is it a thin wrapper forcing the caller to orchestrate? Thin wrappers are shallow.
- **Temporal coupling**: must the caller invoke this together with other functions in a specific order to get a useful result? If yes, the module has leaked its internal timeline. Fix: collapse the sequence behind one entry point and make the ordered helpers private.
- **Configuration leakage**: does the caller have to know edge cases, flags, or internal modes to use this correctly? Fix: pull the complexity down; expose a simple default and hide the rest.

## 3. Check orthogonality

Pick a plausible future change (new data source, new output format, a renamed field, a swapped dependency). Trace which files would need edits. If a single conceptual change requires touching three unrelated files, the module boundaries are wrong. Record the concrete change and the files it touches.

## 4. Check information hiding

For each piece of knowledge the module represents (a magic number, a data shape, an algorithm choice): where does it live? If it is duplicated across files, or if callers must re-encode it to use the module, flag it. Each piece of knowledge exists in exactly one place.

## 5. Check error surface

List the exceptions the module raises or propagates. For each, decide: is this a true contract violation, or an expected variant of normal operation? Expected variants should be handled internally (return empty collection, idempotent deletion, sensible default). Real violations should raise typed exceptions with enough context to diagnose.

## 6. Produce the findings

For each issue, write:
- **What** the issue is (one sentence, concrete).
- **Why** it matters (which caller pays the cost, or which change becomes hard).
- **Suggested fix** (the specific restructuring, not "refactor this").

Do not rewrite the code in this pass; the output is a review, not a patch. Only patch after the user confirms which findings to act on.

# Red flags to always call out

- A module exporting three functions that must be called in order.
- Any file named `utils.py`, `helpers.py`, `common.py`, `misc.py`.
- Functions whose return type varies by input (sometimes `float`, sometimes `None`, sometimes a string).
- Configuration objects or dictionaries passed through three or more function layers without being used at the intermediate levels.
- Exception handling that catches `Exception` or `BaseException`.
- Magic numbers without a comment explaining what they mean and what changing them affects.
