import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import type { AttributionResponse, FillsResponse, PositionsResponse } from "../api";
import { renderWithClient as render } from "../test/renderWithClient";
import { jsonGet, notMocked, server } from "../test/server";
import { PositionsPage } from "./Positions";

const ATTRIBUTION_POPULATED: AttributionResponse = {
  trade_date: "2026-06-15",
  portfolio_id: null,
  level: "book",
  contract_key: "__book__",
  found: true,
  terms: [
    { name: "Delta", dollars: 1200, unit: "$" },
    { name: "Gamma", dollars: -300, unit: "$" },
    { name: "Vega", dollars: 450, unit: "$" },
  ],
  residual: { dollars: 25, unit: "$ (residual vs full reprice)" },
  verdict: {
    within_tolerance: true,
    residual_abs_tol: 100,
    residual_rel_tol: 0.05,
  },
};

const POSITIONS: PositionsResponse = {
  source: "fills",
  source_ts: "2026-06-15T18:00:00+00:00",
  n_lines: 1,
  lines: [
    {
      contract_key: "SPX|OPT|USD|CBOE|100|d|2026-09-18|5200|P",
      underlying: "SPX",
      strike: 5200,
      expiry: "2026-09-18",
      option_right: "P",
      multiplier: 100,
      quantity: 2,
      broker_contract_id: "222",
      mark_price: 12.5,
      market_value: 2500,
      greeks: {
        delta: { raw: -0.3, position: -60, dollar: -585, unit: "$ per $1 of underlying" },
        gamma: { raw: 0.02, position: 4, dollar: 76, unit: "$ per 1% move" },
        vega: { raw: 0.31, position: 62, dollar: 31, unit: "$ per 1 vol point" },
        theta: { raw: -0.05, position: -10, dollar: -4.1, unit: "$ per calendar day" },
        rho: { raw: 0.04, position: 8, dollar: 5, unit: "$ per 1% rate" },
      },
    },
  ],
  book: {
    delta: { dollar: -585, unit: "$ per $1 of underlying" },
    gamma: { dollar: 76, unit: "$ per 1% move" },
    vega: { dollar: 31, unit: "$ per 1 vol point" },
    theta: { dollar: -4.1, unit: "$ per calendar day" },
    rho: { dollar: 5, unit: "$ per 1% rate" },
    market_value: 2500,
  },
  priced_contract_keys: 1,
  unpriced_contract_keys: ["SPX|OPT|USD|CBOE|100|d|2026-12-18|4800|C"],
};

const FILLS: FillsResponse = {
  trade_date: null,
  underlying: "SPX",
  n_fills: 1,
  fills: [
    {
      fill_id: "f-1",
      booking_id: "bk-9",
      source_basket_id: "basket-SPX",
      trade_date: "2026-06-15",
      underlying: "SPX",
      contract_key: "SPX|OPT|USD|CBOE|100|d|2026-09-18|5200|P",
      signed_qty: "2",
      price: 12.5,
      fill_ts: "2026-06-15T17:30:01+00:00",
      mode: "paper",
      broker_contract_id: "222",
    },
  ],
};

test("renders the book summary, positions table and fills ledger", async () => {
  server.use(jsonGet("/api/positions", POSITIONS), jsonGet("/api/positions/fills", FILLS));
  render(<PositionsPage />);

  expect(await screen.findByText("Book summary")).toBeInTheDocument();
  const summary = await screen.findByRole("table", { name: /Book dollar Greeks/i });

  expect(within(summary).getByText("2.5 × 10³")).toBeInTheDocument();

  const positions = await screen.findByRole("table", { name: /Open positions/i });
  expect(within(positions).getByText("SPX P 5.2 × 10³ 2026-09-18")).toBeInTheDocument();

  const ledger = await screen.findByRole("table", { name: /Fills ledger/i });
  expect(within(ledger).getByText("2026-06-15T17:30:01+00:00")).toBeInTheDocument();
});

test("labels the booked-but-unpriced legs rather than hiding them", async () => {
  server.use(jsonGet("/api/positions", POSITIONS), jsonGet("/api/positions/fills", FILLS));
  render(<PositionsPage />);

  const notice = await screen.findByRole("alert", { name: /unpriced legs/i });
  expect(within(notice).getByText(/Booked but unpriced legs \(1\)/)).toBeInTheDocument();
  expect(within(notice).getByText("SPX|OPT|USD|CBOE|100|d|2026-12-18|4800|C")).toBeInTheDocument();
});

test("the underlying and trade-date selectors are present", async () => {
  server.use(jsonGet("/api/positions", POSITIONS), jsonGet("/api/positions/fills", FILLS));
  render(<PositionsPage />);

  expect(await screen.findByLabelText("Underlying")).toBeInTheDocument();
  expect(screen.getByLabelText("Trade date")).toBeInTheDocument();
});

test("a positions fetch error renders through AsyncBlock, not a blank page", async () => {
  server.use(http.get("/api/positions", notMocked), jsonGet("/api/positions/fills", FILLS));
  render(<PositionsPage />);

  await waitFor(() => {
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
  });
  expect(
    screen.getAllByRole("alert").some((el) => /error|failed|500/i.test(el.textContent ?? "")),
  ).toBe(true);
});

// Broker reconciliation moved here from the old Risk Scenarios page: it is an account-wide integrity
// check on the real book (broker snapshot vs our fills-based book), so it belongs next to the book.

test("the broker reconciliation panel reads back the default agreeing snapshot", async () => {
  render(<PositionsPage />);
  expect(await screen.findByText(/Does the broker agree with our book/i)).toBeInTheDocument();
  expect(await screen.findByText("In agreement")).toBeInTheDocument();
});

test("no broker account captured is a plain empty state, not an error alert", async () => {
  server.use(
    http.get("/api/reconciliation", () =>
      HttpResponse.json({ error: "no_broker_account", detail: "none" }, { status: 400 }),
    ),
  );
  render(<PositionsPage />);
  expect(
    await screen.findByText(/No broker account snapshot has been captured yet/i),
  ).toBeInTheDocument();
});

// The P&L decomposition leads the page (front and centre): it answers "what did my risk pay or
// cost, by Greek" before the book summary or the positions table.

test("the P&L decomposition panel renders its labelled-empty state by default", async () => {
  server.use(jsonGet("/api/positions", POSITIONS), jsonGet("/api/positions/fills", FILLS));
  render(<PositionsPage />);
  expect(await screen.findByText("Where the P&L came from")).toBeInTheDocument();
  expect(await screen.findByText(/No P&L attribution for this selection yet/i)).toBeInTheDocument();
});

test("the P&L decomposition leads the page, ahead of the book summary", async () => {
  server.use(jsonGet("/api/positions", POSITIONS), jsonGet("/api/positions/fills", FILLS));
  render(<PositionsPage />);

  const attribution = await screen.findByText("Where the P&L came from");
  const summary = await screen.findByText("Book summary");
  // front-and-centre: the attribution heading precedes the book summary heading in document order.
  expect(attribution.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
    Node.DOCUMENT_POSITION_FOLLOWING,
  );
});

test("the P&L decomposition draws its by-Greek waterfall when an attribution lands", async () => {
  server.use(
    jsonGet("/api/positions", POSITIONS),
    jsonGet("/api/positions/fills", FILLS),
    jsonGet("/api/attribution", ATTRIBUTION_POPULATED),
  );
  render(<PositionsPage />);

  const panel = await screen.findByRole("article", { name: "P&L attribution" });
  expect(within(panel).getByText("within tolerance")).toBeInTheDocument();
  const chart = await screen.findByLabelText(/P&L attribution waterfall, Whole book/i);
  expect(within(chart).getByTestId("plot-types")).toHaveTextContent("waterfall");
});
