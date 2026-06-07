import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

// Plotly draws to a canvas jsdom does not implement; swap the Plot wrapper for a DOM stub that
// exposes the self-label and trace types as text (see src/test/plotMock.tsx).
vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import { HomePage } from "./Home";
import {
  ANALYTICS_AAA,
  CONSTITUENTS_TWO,
  PRICE_HISTORY_AAA,
  RECORDED_EMPTY,
  RECORDED_TWO_DATES,
} from "../test/fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
});

// Route the stubbed fetch by URL path so each of the four endpoints returns its own fixture.
function mockEndpoints(overrides: Partial<Record<string, unknown>> = {}): void {
  const table: Record<string, unknown> = {
    "/api/recorded-dates": RECORDED_TWO_DATES,
    "/api/constituents": CONSTITUENTS_TWO,
    "/api/price-history": PRICE_HISTORY_AAA,
    "/api/analytics": ANALYTICS_AAA,
    ...overrides,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const path = new URL(input, "http://localhost").pathname;
      const value = table[path];
      const ok = value !== undefined;
      return Promise.resolve({
        ok,
        status: ok ? 200 : 500,
        statusText: ok ? "OK" : "Server Error",
        json: async () => value ?? { error: "not mocked" },
      } as Response);
    }),
  );
}

test("renders the recorded-days counter and a date dropdown", async () => {
  mockEndpoints();
  render(<HomePage />);
  expect(await screen.findByLabelText("recorded-count")).toHaveTextContent("2 days recorded");
  const dropdown = await screen.findByLabelText("as-of date");
  expect(within(dropdown).getByText("2026-05-29")).toBeInTheDocument();
});

test("renders the constituent list and it is scrollable", async () => {
  mockEndpoints();
  render(<HomePage />);
  const region = await screen.findByRole("region", { name: /constituents/i });
  // Both seeded names render, price-first (AAA before BBB).
  expect(within(region).getByText("AAA")).toBeInTheDocument();
  expect(within(region).getByText("BBB")).toBeInTheDocument();
  // Bounded, scrollable container so a large basket stays usable.
  expect(region).toHaveStyle({ overflowY: "auto" });
});

test("selecting a ticker renders candlestick, 3D surface, accordion + smile, and dollar Greeks", async () => {
  mockEndpoints();
  const user = userEvent.setup();
  render(<HomePage />);

  await user.click(await screen.findByRole("button", { name: "AAA" }));

  // The price-first detail layout: candlestick first.
  const candle = await screen.findByLabelText(/daily price \(OHLC candlestick\)/i);
  expect(within(candle).getByTestId("plot-types")).toHaveTextContent("candlestick");

  // The 3D IV surface (mesh3d trace).
  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("mesh3d");

  // The per-maturity smile (scatter trace) inside the accordion.
  const smile = await screen.findByLabelText(/Smile — 3m/i);
  expect(within(smile).getByTestId("plot-types")).toHaveTextContent("scatter");

  // The dollar Greeks with their unit strings visible (P0.2 / ADR 0036).
  const greeks = await screen.findByRole("table", { name: /Dollar Greeks/i });
  expect(within(greeks).getByText("$ per 1% move")).toBeInTheDocument();
  expect(within(greeks).getByText("$ per calendar day")).toBeInTheDocument();
  expect(within(greeks).getByText("$ per 1 vol point")).toBeInTheDocument();
});

test("renders a labeled empty state when no dates are recorded", async () => {
  mockEndpoints({ "/api/recorded-dates": RECORDED_EMPTY });
  render(<HomePage />);
  expect(await screen.findByLabelText("recorded-count")).toHaveTextContent("0 days recorded");
  expect(await screen.findByText(/No completed capture runs/i)).toBeInTheDocument();
});

test("a fetch error renders through AsyncBlock, not a blank page", async () => {
  mockEndpoints({ "/api/recorded-dates": undefined });
  render(<HomePage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent("Failed to load");
  });
});
