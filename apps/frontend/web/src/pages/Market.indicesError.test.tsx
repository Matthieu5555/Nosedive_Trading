import { render, screen } from "@testing-library/react";
import { http } from "msw";
import { afterEach, expect, test, vi } from "vitest";

// Plotly and lightweight-charts draw to a canvas jsdom does not implement; swap each wrapper for a
// DOM stub, as the other Market tests do — without these, importing the page crashes on load.
vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock(
  "../components/LightweightLineChart",
  async () => await import("../test/lightweightLineMock"),
);

import { notMocked, server } from "../test/server";
import { MarketPage, resetConstituentHistoryBatchCacheForTests } from "./Market";

// The regression net for the reported failure: /api/indices 500s (a stale/broken backend) and the
// Market page used to render a disabled, empty dropdown over a blank body — no spinner, no error,
// nothing to click and no word why. The index list gates the whole page, so its failure must front
// a visible error instead of being silently swallowed.

afterEach(() => resetConstituentHistoryBatchCacheForTests());

test("a failing /api/indices fronts a visible error tile, not a silent blank page", async () => {
  server.use(http.get("/api/indices", () => notMocked()));

  render(<MarketPage />);

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/not mocked/);
});

test("the index selector is disabled when the index list cannot load", async () => {
  server.use(http.get("/api/indices", () => notMocked()));

  render(<MarketPage />);

  // Wait for the failure to surface, then confirm the dropdown is disabled — but now ALONGSIDE the
  // explanation, not in lonely silence.
  await screen.findByRole("alert");
  expect(screen.getByRole("combobox", { name: "Index" })).toBeDisabled();
});
