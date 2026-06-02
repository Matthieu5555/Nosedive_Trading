---
name: write-tests
description: Write tests for code with independently derived expected values, parameterized inputs, tolerances for float comparisons, and user-facing assertions for UI. Use when adding tests for new functionality, when a task involves numerical or financial computation, when building UI components or pages, or when the user asks for a test suite. Picks unit, component, integration, property-based, contract, or end-to-end style based on what is being tested.
---

# Process

## 1. Decide the test level

Pick the lowest level that catches the class of bug you are worried about. Use multiple levels when they cover different failure modes; do not skip integration or end-to-end just because unit tests exist.

- **Unit**: pure function or isolated class, deterministic, no I/O. Default for computation (pytest, vitest, jest).
- **Component** (frontend): a single React/Vue/Svelte component rendered in isolation with real dependencies where cheap and doubles where not. Uses Testing Library or equivalent. Tests user-visible behavior: what the user sees, types, and clicks.
- **Integration**: multiple modules wired together, real fakes for external systems (in-memory DB, MSW for HTTP), no mocks of the code under test.
- **Property-based** (`hypothesis`, `fast-check`): invariants that should hold across many inputs (monotonicity, idempotence, inverse relationships, conservation laws, parse/serialize round-trips).
- **Contract**: the shape of an API response or message schema validated against a fixed spec (OpenAPI, JSON Schema, Pact). Catches drift between a server and its clients.
- **End-to-end**: the full path a user takes. Use Playwright for browser flows; a real HTTP client against a test-mode server for APIs.
- **Visual regression** (Playwright screenshots, Chromatic, Percy): catches unintended UI changes. Useful but noisy; reserve for components where pixel-level correctness matters.

## 2. Derive expected values independently

This is the rule that catches the most bugs. Never compute the expected value by running the code under test and copying the output.

- **Numerical**: work the example out by hand, in a notebook with a different implementation, or from a textbook reference. For a mean-variance optimizer, solve a 2-asset case analytically and use those weights as the expected output. For a rolling statistic, compute the first window by hand and verify the test data reproduces it.
- **UI**: describe the expected user-visible outcome in plain words before writing the assertion ("after clicking Submit on an empty form, an error with the text 'Name is required' is visible and the Submit button is re-enabled"). The assertion follows directly from that description.
- **Contracts and schemas**: expected values come from the canonical spec, not from a captured response.

Write a comment citing where the expected value came from (`# Hand-calculated: ...`, `# Reference: Markowitz 1952, example 3.1`, `# From OpenAPI spec v3.2, GET /users response`).

## 3. Use tolerances for floats

Never `==` on floats. In Python, use `pytest.approx(expected, rel=1e-9, abs=1e-12)` or `numpy.testing.assert_allclose` with explicit `rtol` and `atol` chosen to match the algorithm's numerical precision. In JS/TS, use `toBeCloseTo` or a helper with explicit tolerances. Justify unusually loose tolerances in a comment.

## 4. Parameterize

Use `@pytest.mark.parametrize`, `test.each`, or `it.each` with named cases. Each case has a label explaining what it tests ("zero-variance asset", "two identical assets", "empty form on submit", "network timeout during load"). Do not hide multiple assertions behind a single anonymous tuple.

## 5. Cover the edge cases

**Numeric**: empty input, single element, boundary values (0, 1, -1, max, min), NaN and inf where relevant, dimensionally degenerate cases (1xN matrix, singular matrix, constant series).

**Date and time**: start-of-series, end-of-series, gaps, duplicate timestamps, DST transitions if wall-clock time matters, timezone boundaries.

**UI**: empty state, loading state, error state, long content (truncation, overflow), short content, keyboard-only navigation, disabled/enabled transitions, rapid repeated interactions (double-click, double-submit), slow network, offline.

**APIs**: 2xx, 4xx, 5xx, timeout, malformed response, partial response, pagination boundaries, empty result sets.

## 6. Strong assertions

Assert the full shape and value, not "not None" or "length > 0". If a function returns a `DataFrame`, assert on the index, columns, and values. For UI, assert that the specific visible text or role is present, not that the container rendered anything. Weak assertions are worse than no test because they create false confidence.

## 7. Test behavior, not implementation

Especially for UI and integration tests: query the way a user or an accessibility tool would. Prefer `getByRole`, `getByLabelText`, `getByText` over CSS selectors or test IDs. Reach for `data-testid` only when no semantic query works; when you do, that is a signal the component may be missing an accessible role or label.

Do not assert on internal state, private methods, CSS class names, or snapshot blobs as the primary check. If the test passes when the UI is broken but the internals are unchanged, the test is measuring the wrong thing.

## 8. Handle async correctly

No arbitrary sleeps (`page.waitForTimeout(1000)`, `time.sleep(2)`). Wait for the condition you actually care about: an element appearing, a request completing, a value settling. Playwright's auto-waiting locators and `expect(locator).toBeVisible()` do this. Testing Library's `findBy*` and `waitFor` do this. In Python, use bounded polling, not fixed sleeps.

Tests that use real timeouts are slow and flaky; every sleep is a bug waiting to happen.

## 9. Fixtures for shared setup, not for hiding inputs

Fixtures for expensive setup (database connection, compiled model, authenticated browser context) or genuinely shared state. Do not hide test inputs in fixtures; inputs belong in the test body or in parametrize so the reader sees what is being tested without navigating.

## 10. Isolation

Each test sets up and tears down its own state. No test depends on another test running first. Run the suite in random order periodically to flush out hidden dependencies. For E2E, each test gets a fresh page or context; shared auth state can be set up once, but user-visible state is reset between tests.

## 11. Run it, see it fail, see it pass

Write the assertion first with the expected value. Run the test; confirm it fails with a clear error. Then implement or fix. A test that has never been seen to fail is not known to work.

# Anti-patterns

- Computing the expected value inside the test with the same code under test, then asserting equality. This only tests that the function is deterministic.
- `assert result is not None` or `expect(element).toBeTruthy()` as the only assertion.
- Mocking the function or component under test.
- Using real external services (paid APIs, production databases, third-party auth) in the default test run. Gate those behind a marker or a separate suite.
- Tests that pass regardless of the code's correctness because the assertion is too weak or the input too trivial.
- `page.waitForTimeout`, `time.sleep`, or any arbitrary wait instead of waiting for a real condition.
- Querying UI by CSS selector (`.btn-primary`, `#submit`) when a role, label, or text query would work.
- Snapshot tests as the primary behavioral assertion. They catch unintended changes but do not specify correct behavior; a broken feature gets silently baked into the new snapshot.
- Shared mutable state between tests (module-level variables, unreset database rows).
- Assertions on implementation details (internal state, private methods, class names) that break on every refactor despite the behavior being correct.
