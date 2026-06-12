import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// Swap the canvas chart wrappers for DOM stubs (jsdom has no canvas), as the other Market tests do.
vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock("../components/LightweightLineChart", async () => await import("../test/lightweightLineMock"));

// Force the volatility-analytics panel to throw on render, while leaving the index-history panel
// (its module sibling) a working stub — so we can prove one panel's crash is contained.
vi.mock("./market/IndexAnalytics", () => ({
  IndexAnalytics: () => {
    throw new Error("plotly choked on a degenerate vol-surface cell");
  },
  IndexHistory: () => <div data-testid="index-history">index history rendered</div>,
}));

import { MarketPage, resetConstituentHistoryBatchCacheForTests } from "./Market";
import {
  CONSTITUENTS_TWO,
  PRICE_HISTORY_BATCH_TWO,
  PRICE_HISTORY_AAA,
  RECORDED_TWO_DATES,
} from "../test/fixtures";

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const table: Record<string, unknown> = {
    "GET /api/recorded-dates": RECORDED_TWO_DATES,
    "GET /api/constituents": CONSTITUENTS_TWO,
    "GET /api/price-history": PRICE_HISTORY_AAA,
    "POST /api/price-history/batch": PRICE_HISTORY_BATCH_TWO,
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
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  resetConstituentHistoryBatchCacheForTests();
});

test("a crash in the analytics panel is contained — sibling panels still render", async () => {
  render(<MarketPage />);

  // The analytics panel degrades to its labelled tile instead of unwinding the page.
  expect(await screen.findByText(/Volatility analytics failed to render\./i)).toBeInTheDocument();

  // The index-history panel and the constituents panel — independent boundaries — are unaffected.
  expect(await screen.findByTestId("index-history")).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Constituents" })).toBeInTheDocument(),
  );
});
