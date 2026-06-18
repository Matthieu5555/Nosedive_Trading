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

// The prominent ticker selector leads the page: the index/ETF itself plus each constituent, as a
// radiogroup, with the index the landing pick. This is the dominant filter promoted to a selector.
test("a prominent ticker selector leads with the index plus each constituent", async () => {
  render(<MarketPage />);

  const selector = await screen.findByRole("radiogroup", { name: "Ticker" });
  // The index/ETF (SPX) is selectable as a ticker, alongside the members AAA / BBB (which arrive with
  // the constituents fetch, so await them).
  expect(within(selector).getByRole("radio", { name: "SPX" })).toBeInTheDocument();
  expect(await within(selector).findByRole("radio", { name: "AAA" })).toBeInTheDocument();
  expect(within(selector).getByRole("radio", { name: "BBB" })).toBeInTheDocument();
  // The index is the landing read (the page opens on the ETF, not a member).
  expect(within(selector).getByRole("radio", { name: "SPX" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  expect(within(selector).getByRole("radio", { name: "AAA" })).toHaveAttribute(
    "aria-checked",
    "false",
  );
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

  const selector = await screen.findByRole("radiogroup", { name: "Ticker" });
  // Landing: only the index (SPX) is requested.
  await waitFor(() => expect(requested).toContain("SPX"));
  expect(requested).not.toContain("AAA");

  await user.click(within(selector).getByRole("radio", { name: "AAA" }));

  // After selecting AAA, the analytics fetch is keyed to AAA, and the chip reads active.
  await waitFor(() => expect(requested).toContain("AAA"));
  expect(within(selector).getByRole("radio", { name: "AAA" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  // The surface heading self-describes the active ticker, not the index.
  expect(
    await screen.findByRole("heading", { name: "Volatility surface, AAA" }),
  ).toBeInTheDocument();
});

// The active ticker re-renders the surface heading, then returns to the index when the index chip is
// picked again (the index is selectable as a ticker too).
test("selecting the index chip returns the active ticker to the index/ETF", async () => {
  server.use(
    jsonGet("/api/analytics", ANALYTICS_SCORECARD),
    http.get("/api/analytics", ({ request }) => {
      const u = new URL(request.url).searchParams.get("underlying");
      return HttpResponse.json({ ...ANALYTICS_SCORECARD, underlying: u });
    }),
  );
  const user = userEvent.setup();
  render(<MarketPage />);

  const selector = await screen.findByRole("radiogroup", { name: "Ticker" });
  await user.click(await within(selector).findByRole("radio", { name: "BBB" }));
  expect(
    await screen.findByRole("heading", { name: "Volatility surface, BBB" }),
  ).toBeInTheDocument();

  await user.click(within(selector).getByRole("radio", { name: "SPX" }));
  expect(
    await screen.findByRole("heading", { name: "Volatility surface, SPX" }),
  ).toBeInTheDocument();
  expect(within(selector).getByRole("radio", { name: "SPX" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
});

// Switching the index re-lands the active ticker on the new index, so a member picked under one index
// can never linger as the active ticker for another.
test("switching the index re-lands the active ticker on the new index", async () => {
  server.use(
    jsonGet("/api/indices", {
      indices: [
        { symbol: "SPX", name: "S&P 500", currency: "USD" },
        { symbol: "SX5E", name: "EURO STOXX 50", currency: "EUR" },
      ],
    }),
    http.get("/api/analytics", ({ request }) => {
      const u = new URL(request.url).searchParams.get("underlying");
      return HttpResponse.json({ ...ANALYTICS_SCORECARD, underlying: u });
    }),
  );
  const user = userEvent.setup();
  render(<MarketPage />);

  const selector = await screen.findByRole("radiogroup", { name: "Ticker" });
  await user.click(await within(selector).findByRole("radio", { name: "AAA" }));
  await screen.findByRole("heading", { name: "Volatility surface, AAA" });

  // Switch the index to SX5E: the active ticker re-lands on SX5E, dropping the stale member.
  await user.selectOptions(screen.getByLabelText("Index"), "SX5E");
  expect(
    await screen.findByRole("heading", { name: "Volatility surface, SX5E" }),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByRole("radio", { name: "SX5E" })).toHaveAttribute("aria-checked", "true"),
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
  const selector = screen.getByRole("radiogroup", { name: "Ticker" });
  expect(within(selector).getByRole("radio", { name: "BBB" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
});
