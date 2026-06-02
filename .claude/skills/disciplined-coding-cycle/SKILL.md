---
name: disciplined-coding-cycle
description: Run one complete development cycle: probe the environment, understand the existing code, plan the change, write useful tests, implement, verify, run quality checks, update documentation, and prepare a git commit. Use for any non-trivial coding task, especially new features, refactors, numerical code, API changes, pipelines, or anything touching multiple files.
---

# Process

## 1. Probe the environment

Use `probe-environment` before writing code whenever the task depends on an unverified runtime, library, API, dataset, file layout, or framework behavior.

Record the verified facts briefly:
- relevant library versions;
- key function signatures;
- dataset schema or API response shape;
- test command availability;
- project structure and entry points.

If the probe contradicts the intended approach, stop and revise the plan before coding.

## 2. Understand the current code

Read the files that define the behavior being changed and the files that call into them.

Identify:
- the public interfaces involved;
- the data structures passed around;
- existing tests;
- existing conventions for naming, typing, error handling, logging, and configuration;
- the smallest coherent place to make the change.

Do not add code until you can state what already exists and where the new behavior belongs.

## 3. Plan before coding

Use `plan-before-coding` for any non-trivial change.

The plan must include:
- what will be changed;
- where the change lives;
- how each step will be verified;
- alternatives considered and rejected.

Stop after presenting the plan. Wait for explicit confirmation before implementing.

## 4. Write or update tests first

Use `write-tests`.

Choose the lowest test level that catches the relevant failure:
- unit tests for pure computation;
- integration tests for module wiring;
- component or end-to-end tests for UI behavior;
- contract tests for API/schema boundaries;
- property-based tests for invariants.

Expected values must be independently derived, not copied from the implementation.

For numerical code, use explicit tolerances. For UI code, assert user-visible behavior. For APIs, assert full response shape and important error cases.

Run the tests before implementation and confirm the relevant new test fails for the right reason.

## 5. Implement one step

Implement only the next confirmed step from the plan.

Keep the change narrow:
- preserve public interfaces unless the plan explicitly changes them;
- avoid unrelated cleanup;
- avoid speculative abstraction;
- keep configuration and domain knowledge in one place;
- use typed exceptions for real contract violations;
- handle expected variants in normal flow.

## 6. Test the code

Run the smallest relevant test first, then broaden.

Typical order:
```bash
uv run pytest path/to/test_file.py -q
uv run pytest -q
```

For frontend projects, use the project's actual commands, for example:

```bash
npm test
npm run test:e2e
npm run lint
```

If a test fails, diagnose the cause. Do not weaken the test unless the expected behavior was wrong, and explain that correction.

## 7. Run quality checks

For Python, use `python-quality-gate`.

Check:

* type hints;
* return type consistency;
* structured data;
* naming;
* path handling;
* logging;
* configuration;
* error handling;
* function shape;
* immutability;
* dependency injection;
* test coverage.

For design-sensitive modules, use `review-module-depth` before declaring the implementation done.

Do not auto-patch quality findings that require design judgment without confirmation.

## 8. Update documentation

Use `write-readme` when the user-facing behavior, setup, configuration, commands, data flow, or limitations changed.

Update:

* README;
* `.env.example`;
* docstrings only where they explain non-obvious contracts;
* changelog or migration notes if the project has them.

Verify every documented command that you claim works. If you could not verify it, say so.

## 9. Prepare the git commit

Inspect the diff before committing:

```bash
git status
git diff
git diff --staged
```

Stage only relevant files.

Write a commit message that states the behavior change, not just the files changed:

```bash
git add <relevant-files>
git commit -m "Add validated rolling volatility calculation"
```

Only create the commit if the user explicitly asked for commits or previously authorized committing in this coding session.

## 10. Report the result

Summarize:

* what changed;
* which tests/checks passed;
* which files were touched;
* any known limitations;
* whether docs were updated;
* whether a git commit was created.

Do not claim success unless the verification actually ran and passed.

# Anti-patterns

* Coding before probing an unfamiliar environment.
* Writing a plan and then silently changing it mid-task.
* Implementing before a failing test exists.
* Testing only the happy path.
* Copying expected values from the code under test.
* Running only the one test that passes and ignoring the broader suite.
* Updating docs from intent rather than actual behavior.
* Making a git commit without explicit authorization.
* Mixing unrelated cleanup into the feature diff.
* Declaring completion without listing the verification performed.

# Note on scope

This skill is a lifecycle controller. It does not duplicate the detailed rules of
each sub-skill; it tells you when to invoke them and what "done" means.
