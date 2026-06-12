import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { useFetch } from "./useFetch";

// A tiny probe component that renders the hook's state machine flat into the DOM, so a test can
// assert on data / error / stale without a page.
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

function okResponse(body: unknown): Response {
  return { ok: true, json: async () => body } as unknown as Response;
}

function errorResponse(status: number): Response {
  return { ok: false, status, statusText: "boom", json: async () => ({}) } as unknown as Response;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("a successful load exposes the data and clears loading", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse({ value: 42 })));
  render(<Probe path="/api/thing" />);
  await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("42"));
  expect(screen.getByTestId("loading")).toHaveTextContent("false");
  expect(screen.getByTestId("error")).toHaveTextContent("none");
  expect(screen.getByTestId("stale")).toHaveTextContent("false");
});

test("a first-load failure fronts an error, not stale", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(errorResponse(500)));
  render(<Probe path="/api/thing" />);
  await waitFor(() => expect(screen.getByTestId("error")).toHaveTextContent("500"));
  expect(screen.getByTestId("value")).toHaveTextContent("none");
  expect(screen.getByTestId("stale")).toHaveTextContent("false");
});

test("a failed background refresh marks the data stale and keeps it on screen", async () => {
  // First poll succeeds (data banked), the next fails: the panel must keep the good value and
  // raise `stale`, not blank to an error — the silent-refresh-failure gap this hook now closes.
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(okResponse({ value: 7 }))
    .mockResolvedValue(errorResponse(503));
  vi.stubGlobal("fetch", fetchMock);
  render(<Probe path="/api/thing" refreshMs={20} />);

  await waitFor(() => expect(screen.getByTestId("value")).toHaveTextContent("7"));
  await waitFor(() => expect(screen.getByTestId("stale")).toHaveTextContent("true"));
  // The known-good value is still rendered, and no error panel was fronted.
  expect(screen.getByTestId("value")).toHaveTextContent("7");
  expect(screen.getByTestId("error")).toHaveTextContent("none");
});
