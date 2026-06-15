import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { server } from "../test/server";
import { useFetch } from "./useFetch";

// A tiny probe component that renders the hook's state machine flat into the DOM, so a test can
// assert on data / error / stale without a page. The probe path is served by per-test msw
// handlers, so the hook's real fetch path (signals, error parsing) is exercised end to end.
function Probe({ path, refreshMs }: { path: string; refreshMs?: number }) {
  const { data, loading, error, stale } = useFetch<{ value: number }>(path, refreshMs);
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="stale">{String(stale)}</span>
      <span data-testid="error">{error ?? "none"}</span>
      <span data-testid="value">{data ? data.value : "none"}</span>
    </div>
  );
}

test("a successful load exposes the data and clears loading", async () => {
  server.use(http.get("/api/thing", () => HttpResponse.json({ value: 42 })));
  render(<Probe path="/api/thing" />);
  await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("42"));
  expect(screen.getByTestId("loading")).toHaveTextContent("false");
  expect(screen.getByTestId("error")).toHaveTextContent("none");
  expect(screen.getByTestId("stale")).toHaveTextContent("false");
});

test("a first-load failure fronts an error, not stale", async () => {
  server.use(http.get("/api/thing", () => HttpResponse.json({ error: "boom" }, { status: 500 })));
  render(<Probe path="/api/thing" />);
  await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent("500"));
  expect(screen.getByTestId("value")).toHaveTextContent("none");
  expect(screen.getByTestId("stale")).toHaveTextContent("false");
});

test("a failed background refresh marks the data stale and keeps it on screen", async () => {
  // First poll succeeds (data banked), every later one fails: the panel must keep the good
  // value and raise `stale`, not blank to an error — the silent-refresh-failure gap this hook
  // now closes. msw matches handlers in order: the `once` success handler answers the first
  // request and is consumed; every later poll falls through to the 503.
  server.use(
    http.get("/api/thing", () => HttpResponse.json({ value: 7 }), { once: true }),
    http.get("/api/thing", () => HttpResponse.json({}, { status: 503 })),
  );
  render(<Probe path="/api/thing" refreshMs={20} />);

  await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("7"));
  await waitFor(() => expect(screen.getByTestId("stale")).toHaveTextContent("true"));
  // The known-good value is still rendered, and no error panel was fronted.
  expect(screen.getByTestId("value")).toHaveTextContent("7");
  expect(screen.getByTestId("error")).toHaveTextContent("none");
});
