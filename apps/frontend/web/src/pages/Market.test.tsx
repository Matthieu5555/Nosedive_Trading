import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));
vi.mock("../components/CandleChart", async () => await import("../test/candleMock"));
vi.mock(
  "../components/LightweightLineChart",
  async () => await import("../test/lightweightLineMock"),
);

import {
  ANALYTICS_AAA_DENSE,
  ANALYTICS_AAA_MONEYNESS_FALLBACK,
  PRICE_HISTORY_BATCH_TWO,
  RECORDED_EMPTY,
} from "../test/fixtures";
import { jsonGet, notMocked, server } from "../test/server";
import { MarketPage, resetConstituentHistoryBatchCacheForTests } from "./Market";

afterEach(() => {
  resetConstituentHistoryBatchCacheForTests();
});

function captureBatchBodies(): unknown[] {
  const bodies: unknown[] = [];
  server.use(
    http.post("/api/price-history/batch", async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json(PRICE_HISTORY_BATCH_TWO);
    }),
  );
  return bodies;
}

test("leads with the index daily-history panel and an as-of date dropdown", async () => {
  render(<MarketPage />);

  expect(await screen.findByLabelText(/SPX daily history/i)).toBeInTheDocument();
  expect(await screen.findByText("2 days recorded")).toBeInTheDocument();
  const dropdown = await screen.findByLabelText("As-of date");
  expect(within(dropdown).getByText("2026-05-29")).toBeInTheDocument();
});

test("renders the point-in-time constituent list, scrollable, price-first", async () => {
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
  render(<MarketPage />);

  const region = await screen.findByRole("region", { name: /constituents/i });
  const aaaRow = within(region).getByText("AAA").closest("tr");
  await waitFor(() => expect(aaaRow).toHaveAttribute("aria-selected", "true"));

  expect(await screen.findByLabelText("Volatility analytics for SPX")).toBeInTheDocument();
});

test("selecting a ticker renders candlestick, 3D surface, accordion + smile, and dollar Greeks", async () => {
  const user = userEvent.setup();
  render(<MarketPage />);

  await user.click(await screen.findByRole("button", { name: "AAA" }));

  const candles = await screen.findAllByLabelText(/daily price \(OHLC candlestick\)/i);
  expect(candles.length).toBeGreaterThanOrEqual(1);
  expect(within(candles[0]).getByTestId("candle-bars")).toHaveTextContent("2");

  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("surface");

  const smile = await screen.findByLabelText(/Smile — 3m/i);

  expect(within(smile).getByTestId("line-series")).toHaveTextContent("puts");
  expect(within(smile).getByTestId("line-series")).toHaveTextContent("calls");

  const deltaPanel = await screen.findByLabelText(/Delta \$ term structure/i);
  expect(within(deltaPanel).getByTestId("line-series")).toHaveTextContent("30dp");
  expect(within(deltaPanel).getByTestId("line-unit")).toHaveTextContent("$ per $1 of underlying");
  expect(
    await screen.findByLabelText(/Gamma \$ term structure \(\$ per 1% move\)/i),
  ).toBeInTheDocument();
  expect(
    await screen.findByLabelText(/Theta \$ term structure \(\$ per calendar day\)/i),
  ).toBeInTheDocument();

  const greeks = await screen.findByRole("table", { name: /Dollar Greeks/i });
  expect(within(greeks).getByText("$ per 1% move")).toBeInTheDocument();
  expect(within(greeks).getByText("$ per calendar day")).toBeInTheDocument();
  expect(within(greeks).getByText("$ per 1 vol point")).toBeInTheDocument();
});

test("the selected component's candlestick renders without waiting for the batch preload", async () => {
  server.use(http.post("/api/price-history/batch", notMocked));
  const user = userEvent.setup();
  render(<MarketPage />);

  await user.click(await screen.findByRole("button", { name: "AAA" }));
  const detail = await screen.findByLabelText("Price history for AAA");
  await waitFor(() => {
    expect(within(detail).getByTestId("candle-bars")).toHaveTextContent("2");
  });
});

test("returning to the page does not re-fire the whole-basket batch preload", async () => {
  const batchBodies = captureBatchBodies();
  const first = render(<MarketPage />);
  await screen.findByLabelText("Price history for AAA");
  first.unmount();
  render(<MarketPage />);
  await screen.findByLabelText("Price history for AAA");

  expect(batchBodies.length).toBe(1);
});

test("the batch preload requests the full ticker symbols, not fragments", async () => {
  const batchBodies = captureBatchBodies();
  render(<MarketPage />);
  await screen.findByLabelText("Price history for AAA");

  expect(batchBodies.length).toBeGreaterThanOrEqual(1);
  const body = batchBodies[0] as { underlyings: string[] };
  expect(body.underlyings).toEqual(["AAA", "BBB"]);
});

test("the grid-fallback smile is labeled as moneyness and flags a degenerate fit", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_AAA_MONEYNESS_FALLBACK));
  render(<MarketPage />);

  const smile = await screen.findByLabelText(/Smile — 0\.250y/i);

  expect(smile.getAttribute("aria-label")).toMatch(/implied vol vs log-moneyness/i);
  expect(smile.getAttribute("aria-label")).toMatch(/degenerate fit/i);

  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
  expect(surface.getAttribute("aria-label")).toMatch(/vol vs log-moneyness vs maturity/i);
});

test("renders the dense reconstructed surface (smooth nappe) when the BFF serves one", async () => {
  server.use(jsonGet("/api/analytics", ANALYTICS_AAA_DENSE));
  render(<MarketPage />);

  const surface = await screen.findByLabelText(/Implied-volatility surface/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("surface");
  expect(within(surface).getByTestId("plot-z")).toHaveTextContent(
    JSON.stringify(ANALYTICS_AAA_DENSE.surface!.implied_vol),
  );
});

test("renders a labeled empty state when no dates are recorded", async () => {
  server.use(jsonGet("/api/recorded-dates", RECORDED_EMPTY));
  render(<MarketPage />);
  expect(await screen.findByText(/No capture runs to show/i)).toBeInTheDocument();
});

test("shows a qc-failing day with a QC fail badge instead of hiding it", async () => {
  server.use(
    jsonGet("/api/recorded-dates", {
      index: "SPX",
      count: 0,
      dates: [],
      available: [{ date: "2026-06-10", qc: "fail" }],
    }),
  );
  render(<MarketPage />);
  expect(await screen.findByLabelText(/SPX daily history/i)).toBeInTheDocument();
  expect(await screen.findByText("QC fail")).toBeInTheDocument();
});

test("a fetch error renders through AsyncBlock, not a blank page", async () => {
  server.use(http.get("/api/recorded-dates", notMocked));
  render(<MarketPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/error|failed|500/i);
  });
});

test("monetized Greeks render in the index's quote currency (€ for SX5E)", async () => {
  server.use(
    jsonGet("/api/indices", {
      indices: [{ symbol: "SX5E", name: "EURO STOXX 50", currency: "EUR" }],
    }),
    jsonGet("/api/recorded-dates", {
      index: "SX5E",
      count: 1,
      dates: ["2026-05-29"],
      available: [{ date: "2026-05-29", qc: "pass" }],
    }),
  );
  render(<MarketPage />);

  const greeks = await screen.findByRole("table", { name: /Dollar Greeks — / });

  expect(within(greeks).getByText("€ per 1% move")).toBeInTheDocument();
  expect(within(greeks).getByText("€ per €1 of underlying")).toBeInTheDocument();

  expect(
    await screen.findByLabelText(/Gamma \$ term structure \(€ per 1% move\)/i),
  ).toBeInTheDocument();
});

test("the index selector is driven by /api/indices — a parked index is not offered", async () => {
  server.use(
    jsonGet("/api/indices", { indices: [{ symbol: "SX5E", name: "EURO STOXX 50" }] }),
    jsonGet("/api/recorded-dates", { index: "SX5E", count: 0, dates: [], available: [] }),
  );
  render(<MarketPage />);
  const select = await screen.findByLabelText("Index");
  expect(
    within(select).getByRole("option", { name: /EURO STOXX 50 \(SX5E\)/ }),
  ).toBeInTheDocument();
  expect(within(select).queryByRole("option", { name: /SPX/ })).not.toBeInTheDocument();
});
