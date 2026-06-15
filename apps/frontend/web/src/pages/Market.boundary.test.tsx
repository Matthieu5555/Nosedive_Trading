import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// Swap the canvas chart wrappers for DOM stubs (jsdom has no canvas), as the other Market tests do.
vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock(
  "../components/LightweightLineChart",
  async () => await import("../test/lightweightLineMock"),
);

// Force the volatility-analytics panel to throw on render, while leaving the index-history panel
// (its module sibling) a working stub — so we can prove one panel's crash is contained.
vi.mock("./market/IndexAnalytics", () => ({
  IndexAnalytics: () => {
    throw new Error("plotly choked on a degenerate vol-surface cell");
  },
  IndexHistory: () => <div data-testid="index-history">index history rendered</div>,
}));

import { MarketPage, resetConstituentHistoryBatchCacheForTests } from "./Market";

// The page's endpoints are served by the msw defaults (src/test/server.ts); only the thrown
// render error is bespoke here.

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
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
