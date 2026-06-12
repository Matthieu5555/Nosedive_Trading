import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

// Plotly and lightweight-charts both draw to a canvas jsdom does not implement; swap each
// wrapper for a DOM stub that exposes the self-label and chart inputs as text.
vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock("../components/LightweightLineChart", async () => await import("../test/lightweightLineMock"));

import { MarketPage } from "./Market";
import {
  ANALYTICS_AAA,
  ANALYTICS_AAA_MONEYNESS_FALLBACK,
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

test("default-selects the heaviest constituent and shows its detail without a click", async () => {
  // CONSTITUENTS_TWO weights: AAA 0.6 > BBB 0.4 — so AAA (the index's heaviest) is selected by
  // default (cahier des charges §3.2), and its analytics render with no interaction.
  mockEndpoints();
  render(<MarketPage />);

  const region = await screen.findByRole("region", { name: /constituents/i });
  const aaaRow = within(region).getByText("AAA").closest("tr");
  await waitFor(() => expect(aaaRow).toHaveAttribute("aria-selected", "true"));
  // The volatility analytics panel is index-keyed (not the selected constituent) — the option
  // chain is captured at the index level — and renders without any click.
  expect(await screen.findByLabelText("Volatility analytics for SPX")).toBeInTheDocument();
});

test("selecting a ticker renders candlestick, 3D surface, accordion + smile, and dollar Greeks", async () => {
  mockEndpoints();
  const user = userEvent.setup();
  render(<MarketPage />);

  await user.click(await screen.findByRole("button", { name: "AAA" }));

  // Candlesticks are present (the index history + the ticker detail both render one), each fed
  // its OHLC bars (the lightweight-charts stub echoes the bar count it received).
  const candles = await screen.findAllByLabelText(/daily price \(OHLC candlestick\)/i);
  expect(candles.length).toBeGreaterThanOrEqual(1);
  expect(within(candles[0]).getByTestId("candle-bars")).toHaveTextContent("2");

  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("mesh3d");

  const smile = await screen.findByLabelText(/Smile — 3m/i);
  expect(within(smile).getByTestId("plot-types")).toHaveTextContent("scatter");

  // The dollar-Greeks term structure renders one TradingView line panel per Greek, with one
  // series per delta band and the $ unit string carried into the panel label.
  const deltaPanel = await screen.findByLabelText(/Delta \$ term structure/i);
  expect(within(deltaPanel).getByTestId("line-series")).toHaveTextContent("30dp");
  expect(within(deltaPanel).getByTestId("line-unit")).toHaveTextContent(
    "$ per $1 of underlying",
  );
  expect(
    await screen.findByLabelText(/Gamma \$ term structure \(\$ per 1% move\)/i),
  ).toBeInTheDocument();
  expect(
    await screen.findByLabelText(/Theta \$ term structure \(\$ per calendar day\)/i),
  ).toBeInTheDocument();

  // Dollar Greeks carry decimal (raw) AND currency, with the unit strings visible (P0.2/OQ-1).
  const greeks = await screen.findByRole("table", { name: /Dollar Greeks/i });
  expect(within(greeks).getByText("$ per 1% move")).toBeInTheDocument();
  expect(within(greeks).getByText("$ per calendar day")).toBeInTheDocument();
  expect(within(greeks).getByText("$ per 1 vol point")).toBeInTheDocument();
});

test("the grid-fallback smile is labeled as moneyness and flags a degenerate fit", async () => {
  // F-BFF-04: when the BFF serves the surface-grid fallback, the axis announces itself as
  // moneyness (never "delta"), and the degenerate calibration is visibly flagged.
  mockEndpoints({ "/api/analytics": ANALYTICS_AAA_MONEYNESS_FALLBACK });
  render(<MarketPage />);

  const smile = await screen.findByLabelText(/Smile — 0\.250y/i);
  expect(smile.getAttribute("aria-label")).toMatch(/implied vol vs moneyness \(log\)/i);
  expect(smile.getAttribute("aria-label")).toMatch(/degenerate fit/i);

  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
  expect(surface.getAttribute("aria-label")).toMatch(/vol vs moneyness \(log\) vs maturity/i);
});

test("renders a labeled empty state when no dates are recorded", async () => {
  mockEndpoints({ "/api/recorded-dates": RECORDED_EMPTY });
  render(<MarketPage />);
  expect(await screen.findByText(/No capture runs to show/i)).toBeInTheDocument();
});

test("shows a qc-failing day with a QC fail badge instead of hiding it", async () => {
  // count==0 (no clean day) but a viewable qc-failing day exists — it must be selectable and
  // shown with its QC badge, not hidden (cahier des charges §3.1/§5).
  mockEndpoints({
    "/api/recorded-dates": {
      index: "SPX",
      count: 0,
      dates: [],
      available: [{ date: "2026-06-10", qc: "fail" }],
    },
  });
  render(<MarketPage />);
  expect(await screen.findByLabelText(/SPX daily history/i)).toBeInTheDocument();
  expect(await screen.findByText("QC fail")).toBeInTheDocument();
});

test("a fetch error renders through AsyncBlock, not a blank page", async () => {
  mockEndpoints({ "/api/recorded-dates": undefined });
  render(<MarketPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/error|failed|500/i);
  });
});
