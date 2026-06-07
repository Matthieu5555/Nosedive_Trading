import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

// Plotly draws to a canvas jsdom does not implement; swap the Plot wrapper for a DOM stub that
// exposes the self-label and trace types as text (see src/test/plotMock.tsx).
vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import { MarketPage } from "./Market";
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

// Route the stubbed fetch by URL path so each endpoint returns its own fixture. (price-history
// is hit for both the index and the ticker; the mock returns the same fixture for both.)
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

test("leads with the index daily-history panel and an as-of date dropdown", async () => {
  mockEndpoints();
  render(<MarketPage />);
  // The index's own daily candlestick leads the page (price-first).
  expect(await screen.findByLabelText(/SPX daily history/i)).toBeInTheDocument();
  expect(await screen.findByText("2 days recorded")).toBeInTheDocument();
  const dropdown = await screen.findByLabelText("As-of date");
  expect(within(dropdown).getByText("2026-05-29")).toBeInTheDocument();
});

test("renders the point-in-time constituent list, scrollable, price-first", async () => {
  mockEndpoints();
  render(<MarketPage />);
  const region = await screen.findByRole("region", { name: /constituents/i });
  expect(within(region).getByText("AAA")).toBeInTheDocument();
  expect(within(region).getByText("BBB")).toBeInTheDocument();
  expect(region).toHaveStyle({ overflowY: "auto" });
});

test("selecting a ticker renders candlestick, 3D surface, accordion + smile, and dollar Greeks", async () => {
  mockEndpoints();
  const user = userEvent.setup();
  render(<MarketPage />);

  await user.click(await screen.findByRole("button", { name: "AAA" }));

  // Candlesticks are present (the index history + the ticker detail both render one).
  const candles = await screen.findAllByLabelText(/daily price \(OHLC candlestick\)/i);
  expect(candles.length).toBeGreaterThanOrEqual(1);
  expect(within(candles[0]).getByTestId("plot-types")).toHaveTextContent("candlestick");

  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("mesh3d");

  const smile = await screen.findByLabelText(/Smile — 3m/i);
  expect(within(smile).getByTestId("plot-types")).toHaveTextContent("scatter");

  // Dollar Greeks carry decimal (raw) AND currency, with the unit strings visible (P0.2/OQ-1).
  const greeks = await screen.findByRole("table", { name: /Dollar Greeks/i });
  expect(within(greeks).getByText("$ per 1% move")).toBeInTheDocument();
  expect(within(greeks).getByText("$ per calendar day")).toBeInTheDocument();
  expect(within(greeks).getByText("$ per 1 vol point")).toBeInTheDocument();
});

test("renders a labeled empty state when no dates are recorded", async () => {
  mockEndpoints({ "/api/recorded-dates": RECORDED_EMPTY });
  render(<MarketPage />);
  expect(await screen.findByText(/No completed capture runs/i)).toBeInTheDocument();
});

test("a fetch error renders through AsyncBlock, not a blank page", async () => {
  mockEndpoints({ "/api/recorded-dates": undefined });
  render(<MarketPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/error|failed|500/i);
  });
});
