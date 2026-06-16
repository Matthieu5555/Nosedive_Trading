import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock(
  "../components/LightweightLineChart",
  async () => await import("../test/lightweightLineMock"),
);

vi.mock("./market/IndexAnalytics", () => ({
  IndexAnalytics: () => {
    throw new Error("plotly choked on a degenerate vol-surface cell");
  },
  IndexHistory: () => <div data-testid="index-history">index history rendered</div>,
}));

import { MarketPage, resetConstituentHistoryBatchCacheForTests } from "./Market";

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  resetConstituentHistoryBatchCacheForTests();
});

test("a crash in the analytics panel is contained — sibling panels still render", async () => {
  render(<MarketPage />);

  expect(await screen.findByText(/Volatility analytics failed to render\./i)).toBeInTheDocument();

  expect(await screen.findByTestId("index-history")).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Constituents" })).toBeInTheDocument(),
  );
});
