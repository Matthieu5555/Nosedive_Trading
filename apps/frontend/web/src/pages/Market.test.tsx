import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

// Plotly and lightweight-charts both draw to a canvas jsdom does not implement; swap each
// wrapper for a DOM stub that exposes the self-label and chart inputs as text.
vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock("../components/LightweightLineChart", async () => await import("../test/lightweightLineMock"));

import { MarketPage, resetConstituentHistoryBatchCacheForTests } from "./Market";
import {
  ANALYTICS_AAA,
  ANALYTICS_AAA_MONEYNESS_FALLBACK,
  CONSTITUENTS_TWO,
  PRICE_HISTORY_BATCH_TWO,
  PRICE_HISTORY_AAA,
  RECORDED_EMPTY,
  RECORDED_TWO_DATES,
} from "../test/fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
  resetConstituentHistoryBatchCacheForTests();
});

// Route the stubbed fetch by method + URL path so each endpoint returns its own fixture. The
// component histories are fetched through the batch endpoint, not one request per row.
function mockEndpoints(overrides: Partial<Record<string, unknown>> = {}): void {
  const table: Record<string, unknown> = {
    "GET /api/recorded-dates": RECORDED_TWO_DATES,
    "GET /api/constituents": CONSTITUENTS_TWO,
    "GET /api/price-history": PRICE_HISTORY_AAA,
    "GET /api/analytics": ANALYTICS_AAA,
    "POST /api/price-history/batch": PRICE_HISTORY_BATCH_TWO,
    ...overrides,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string | Request | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : input;
      const path = new URL(url, "http://localhost").pathname;
      const method = init?.method ?? (input instanceof Request ? input.method : "GET");
      const value = table[`${method} ${path}`];
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
  const coverage = await screen.findByLabelText("Underlying history coverage");
  expect(within(coverage).getByText("2 / 2")).toBeInTheDocument();
  expect(within(coverage).getByText("4")).toBeInTheDocument();
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
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("surface");

  const smile = await screen.findByLabelText(/Smile — 3m/i);
  // The smile renders in TradingView Lightweight Charts (not Plotly) as two wings: puts / calls.
  expect(within(smile).getByTestId("line-series")).toHaveTextContent("puts");
  expect(within(smile).getByTestId("line-series")).toHaveTextContent("calls");

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

test("the selected component's candlestick renders without waiting for the batch preload", async () => {
  // The batch preload is forced to FAIL: the detail panel must still fill through the
  // single-ticker endpoint — on the live store the batch takes ~1 min and a page reload
  // restarts it, so a batch-gated detail never shows.
  mockEndpoints({ "POST /api/price-history/batch": undefined });
  const user = userEvent.setup();
  render(<MarketPage />);

  await user.click(await screen.findByRole("button", { name: "AAA" }));
  const detail = await screen.findByLabelText("Price history for AAA");
  await waitFor(() => {
    expect(within(detail).getByTestId("candle-bars")).toHaveTextContent("2");
  });
});

test("returning to the page does not re-fire the whole-basket batch preload", async () => {
  // The batch costs ~1 min live and keeps running server-side after unmount: page switches
  // must reuse the session-cached preload, never stack a new scan per visit.
  mockEndpoints();
  const first = render(<MarketPage />);
  await screen.findByLabelText("Price history for AAA");
  first.unmount();
  render(<MarketPage />);
  await screen.findByLabelText("Price history for AAA");

  const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
  const batchCalls = fetchMock.mock.calls.filter(([input, init]) => {
    const url = input instanceof Request ? input.url : String(input);
    const method =
      (init as RequestInit | undefined)?.method ??
      (input instanceof Request ? input.method : "GET");
    return method === "POST" && url.includes("/api/price-history/batch");
  });
  expect(batchCalls.length).toBe(1);
});

test("the batch preload requests the full ticker symbols, not fragments", async () => {
  // The request body is the contract: multi-character tickers must arrive intact. A fixed-
  // payload mock hides any key-encoding bug, so this test pins the body itself.
  mockEndpoints();
  render(<MarketPage />);
  await screen.findByLabelText("Price history for AAA");

  const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
  const batchCall = fetchMock.mock.calls.find(([input, init]) => {
    const url = input instanceof Request ? input.url : String(input);
    const method =
      (init as RequestInit | undefined)?.method ??
      (input instanceof Request ? input.method : "GET");
    return method === "POST" && url.includes("/api/price-history/batch");
  });
  expect(batchCall).toBeDefined();
  const body = JSON.parse(String((batchCall?.[1] as RequestInit).body)) as {
    underlyings: string[];
  };
  expect(body.underlyings).toEqual(["AAA", "BBB"]);
});

test("the grid-fallback smile is labeled as moneyness and flags a degenerate fit", async () => {
  // F-BFF-04: when the BFF serves the surface-grid fallback, the axis announces itself as
  // moneyness (never "delta"), and the degenerate calibration is visibly flagged.
  mockEndpoints({ "GET /api/analytics": ANALYTICS_AAA_MONEYNESS_FALLBACK });
  render(<MarketPage />);

  const smile = await screen.findByLabelText(/Smile — 0\.250y/i);
  // The smile is always plotted on log-moneyness (ATM-centred), for either BFF source; the
  // degenerate calibration is still visibly flagged.
  expect(smile.getAttribute("aria-label")).toMatch(/implied vol vs log-moneyness/i);
  expect(smile.getAttribute("aria-label")).toMatch(/degenerate fit/i);

  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
  expect(surface.getAttribute("aria-label")).toMatch(/vol vs moneyness \(log\) vs maturity/i);
});

test("renders a labeled empty state when no dates are recorded", async () => {
  mockEndpoints({ "GET /api/recorded-dates": RECORDED_EMPTY });
  render(<MarketPage />);
  expect(await screen.findByText(/No capture runs to show/i)).toBeInTheDocument();
});

test("shows a qc-failing day with a QC fail badge instead of hiding it", async () => {
  // count==0 (no clean day) but a viewable qc-failing day exists — it must be selectable and
  // shown with its QC badge, not hidden (cahier des charges §3.1/§5).
  mockEndpoints({
    "GET /api/recorded-dates": {
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
  mockEndpoints({ "GET /api/recorded-dates": undefined });
  render(<MarketPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/error|failed|500/i);
  });
});
