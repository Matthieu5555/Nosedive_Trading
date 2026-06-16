import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock(
  "../components/LightweightLineChart",
  async () => await import("../test/lightweightLineMock"),
);

// A crash deep in the analytics view must be contained by the boundary around it — the selector
// strip and tabs (the page chrome) keep rendering, so the page is never blanked.
vi.mock("./market/AnalyticsTab", () => ({
  AnalyticsTab: () => {
    throw new Error("plotly choked on a degenerate vol-surface cell");
  },
}));

import { MarketPage } from "./Market";

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

test("a crash in the analytics view is contained — the selector strip still renders", async () => {
  render(<MarketPage />);

  expect(await screen.findByText(/Analytics failed to render\./i)).toBeInTheDocument();

  // The shared context strip is page chrome, outside the analytics boundary, so it survives.
  await waitFor(() => expect(screen.getByLabelText("Entity")).toBeInTheDocument());
  expect(screen.getByRole("radiogroup", { name: /option side/i })).toBeInTheDocument();
});
