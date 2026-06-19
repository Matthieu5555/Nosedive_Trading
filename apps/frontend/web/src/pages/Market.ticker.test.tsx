import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock(
  "../components/LightweightLineChart",
  async () => await import("../test/lightweightLineMock"),
);

import { ANALYTICS_SCORECARD } from "../test/fixtures";
import { jsonGet, server } from "../test/server";
import { MarketPage } from "./Market";

// The Constituents block is the page's one ticker selector: the index/ETF sits at its top as a
// selectable ticker (the landing pick), each member is a clickable row beside it. The old tile-grid
// picker is gone, the Constituents block is the only way to choose the underlying.
test("the Constituents block is the only ticker selector, leading with the index plus each member", async () => {
  render(<MarketPage />);

  // The index/ETF (SPX) is selectable, at the top of the Constituents block, and is the landing read.
  const indexPick = await screen.findByRole("radiogroup", { name: "Index ticker" });
  expect(within(indexPick).getByRole("radio", { name: "SPX" })).toHaveAttribute(
    "aria-checked",
    "true",
  );

  // Each member (AAA / BBB, from the constituents fetch) is a clickable row in the Constituents table.
  const constituents = await screen.findByRole("region", { name: /constituents/i });
  expect(await within(constituents).findByRole("button", { name: "AAA" })).toBeInTheDocument();
  expect(within(constituents).getByRole("button", { name: "BBB" })).toBeInTheDocument();

  // The redundant tile-grid ticker picker is gone.
  expect(screen.queryByRole("radiogroup", { name: "Ticker" })).not.toBeInTheDocument();
});

// The core mechanism: picking a constituent makes it the active underlying that the analytics panels
// fetch for. The page asks /api/analytics for the chosen member's own surface (the data evidence is
// real: the offline store carries per-constituent surfaces).
test("picking a constituent drives the analytics panels to that ticker's own surface", async () => {
  const requested: string[] = [];
  server.use(
    http.get("/api/analytics", ({ request }) => {
      const u = new URL(request.url).searchParams.get("underlying");
      if (u) requested.push(u);
      return HttpResponse.json({ ...ANALYTICS_SCORECARD, underlying: u });
    }),
  );
  const user = userEvent.setup();
  render(<MarketPage />);

  const constituents = await screen.findByRole("region", { name: /constituents/i });
  // Landing: only the index (SPX) is requested.
  await waitFor(() => expect(requested).toContain("SPX"));
  expect(requested).not.toContain("AAA");

  await user.click(await within(constituents).findByRole("button", { name: "AAA" }));

  // After selecting AAA, the analytics fetch is keyed to AAA, and the surface heading self-describes
  // the active ticker, not the index.
  await waitFor(() => expect(requested).toContain("AAA"));
  expect(
    await screen.findByRole("heading", { name: "Volatility surface, AAA" }),
  ).toBeInTheDocument();
});

// The active ticker re-renders the surface heading, then returns to the index when the index pick at
// the top of the Constituents block is clicked again (the index is selectable as a ticker too).
test("selecting the index pick returns the active ticker to the index/ETF", async () => {
  server.use(
    jsonGet("/api/analytics", ANALYTICS_SCORECARD),
    http.get("/api/analytics", ({ request }) => {
      const u = new URL(request.url).searchParams.get("underlying");
      return HttpResponse.json({ ...ANALYTICS_SCORECARD, underlying: u });
    }),
  );
  const user = userEvent.setup();
  render(<MarketPage />);

  const constituents = await screen.findByRole("region", { name: /constituents/i });
  await user.click(await within(constituents).findByRole("button", { name: "BBB" }));
  expect(
    await screen.findByRole("heading", { name: "Volatility surface, BBB" }),
  ).toBeInTheDocument();

  const indexPick = await screen.findByRole("radiogroup", { name: "Index ticker" });
  await user.click(within(indexPick).getByRole("radio", { name: "SPX" }));
  expect(
    await screen.findByRole("heading", { name: "Volatility surface, SPX" }),
  ).toBeInTheDocument();
  expect(within(indexPick).getByRole("radio", { name: "SPX" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
});

// The index is selectable from the Constituents block too (the canonical selection surface): clicking
// the index pick there returns the whole page to the index, the same as the top chip. The index is the
// landing ticker, so first pick a member, then select the index from the Constituents block.
test("selecting the index from the Constituents block returns the active ticker to the index", async () => {
  server.use(
    http.get("/api/analytics", ({ request }) => {
      const u = new URL(request.url).searchParams.get("underlying");
      return HttpResponse.json({ ...ANALYTICS_SCORECARD, underlying: u });
    }),
  );
  const user = userEvent.setup();
  render(<MarketPage />);

  const constituents = await screen.findByRole("region", { name: /constituents/i });
  await user.click(await within(constituents).findByRole("button", { name: "AAA" }));
  await screen.findByRole("heading", { name: "Volatility surface, AAA" });

  // The index/ETF (SPX) sits at the top of the Constituents block as a selectable ticker.
  const indexPick = await screen.findByRole("radiogroup", { name: "Index ticker" });
  await user.click(within(indexPick).getByRole("radio", { name: "SPX" }));

  expect(
    await screen.findByRole("heading", { name: "Volatility surface, SPX" }),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(within(indexPick).getByRole("radio", { name: "SPX" })).toHaveAttribute(
      "aria-checked",
      "true",
    ),
  );
});

// Clicking a constituent row in the Constituents table is also a page-driving selection: it sets the
// active ticker the same as the top selector (the table doubles as a selection surface).
test("clicking a constituent row in the table also drives the active ticker", async () => {
  server.use(
    http.get("/api/analytics", ({ request }) => {
      const u = new URL(request.url).searchParams.get("underlying");
      return HttpResponse.json({ ...ANALYTICS_SCORECARD, underlying: u });
    }),
  );
  const user = userEvent.setup();
  render(<MarketPage />);

  const constituents = await screen.findByRole("region", { name: /constituents/i });
  await user.click(within(constituents).getByRole("button", { name: "BBB" }));

  expect(
    await screen.findByRole("heading", { name: "Volatility surface, BBB" }),
  ).toBeInTheDocument();
  // The clicked row reads as the selected one in the Constituents table.
  const selectedRow = within(constituents).getByRole("row", { selected: true });
  expect(within(selectedRow).getByRole("button", { name: "BBB" })).toBeInTheDocument();
});
