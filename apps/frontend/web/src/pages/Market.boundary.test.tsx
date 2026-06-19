import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock(
  "../components/LightweightLineChart",
  async () => await import("../test/lightweightLineMock"),
);

// A crash deep in one panel of the scroll (here the scorecards) must be contained by the boundary
// around it — the page chrome (the as-of picker) keeps rendering, so the page is never blanked by a
// single panel choking on a degenerate value.
vi.mock("../components/Scorecards", () => ({
  Scorecards: () => {
    throw new Error("scorecard math choked on a degenerate vol-surface cell");
  },
}));

import { MarketPage } from "./Market";

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

test("a crash in one scroll panel is contained, the page chrome still renders", async () => {
  render(<MarketPage />);

  expect(await screen.findByText(/Scorecards failed to render\./i)).toBeInTheDocument();

  // The as-of picker is page chrome, outside the panel boundary, so it survives.
  await waitFor(() => expect(screen.getByLabelText("As-of fetch")).toBeInTheDocument());
});
