import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, test, vi } from "vitest";

vi.mock("../../components/Plot", async () => await import("../../test/plotMock"));

import type { AttributionResponse, RealizedAttributionResponse } from "../../api";
import { BASKET_RISK_AAA } from "../../test/fixtures";
import { renderWithClient } from "../../test/renderWithClient";
import { jsonGet, jsonPost, server } from "../../test/server";
import { BuildBasket } from "./BuildBasket";

// The ④ Attribution tab now also fires useRealizedAttribution on mount, so every render of the
// page needs a QueryClient in scope; `render` is replaced by `renderWithClient` below. A labelled-
// empty realized payload keeps that auto-firing query off the catch-all 500 so the tab stays clean.
const REALIZED_EMPTY: RealizedAttributionResponse = {
  found: false,
  underlying: "SX5E",
  expiry: "2026-09-18",
  portfolio_id: "demo",
  term_unit: "$",
  residual_unit: "$",
  contracts: [],
  dates: [],
  steps: [],
};

beforeEach(() => {
  server.use(jsonGet("/api/attribution/realized", REALIZED_EMPTY));
});

test("the page splits into the three build-basket blocks (compose → shock → explain), Compose first", () => {
  renderWithClient(<BuildBasket />);

  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((tab) => tab.textContent)).toEqual(["① Compose", "② Stress", "③ Attribution"]);
  expect(screen.getByRole("tab", { name: /compose/i })).toHaveAttribute("data-state", "active");
});

test("the leg composer (templates + grid + controls) is shared above the tabs", async () => {
  const user = userEvent.setup();
  renderWithClient(<BuildBasket />);

  expect(screen.getByLabelText("underlying")).toBeInTheDocument();
  expect(screen.getByLabelText("trade date")).toBeInTheDocument();
  expect(screen.getByLabelText("tenor")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /template straddle/i }));
  const legs = screen.getByRole("table", { name: /composed legs/i });
  expect(within(legs).getByText("atm")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: /stress/i }));
  expect(within(legs).getByText("atm")).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: /attribution/i }));
  expect(within(legs).getByText("atm")).toBeInTheDocument();
});

test("pricing happens on the Compose block and shows the book-additive totals", async () => {
  const user = userEvent.setup();
  server.use(jsonPost("/api/basket/risk", BASKET_RISK_AAA));
  renderWithClient(<BuildBasket />);

  await user.click(screen.getByRole("button", { name: /template straddle/i }));
  await user.click(screen.getByRole("button", { name: /price basket/i }));
  await waitFor(() =>
    expect(screen.getByRole("table", { name: /book-additive sum/i })).toBeInTheDocument(),
  );
});

const ATTRIBUTION_AAA: AttributionResponse = {
  found: true,
  trade_date: "2026-06-05",
  portfolio_id: "demo-book",
  level: "book",
  contract_key: "__book__",
  terms: [
    { name: "Delta", dollars: 1200, unit: "$" },
    { name: "Gamma", dollars: -300, unit: "$" },
    { name: "Vega", dollars: 450, unit: "$" },
    { name: "Theta", dollars: -150, unit: "$" },
  ],
  residual: { dollars: 20, unit: "$" },
  verdict: { within_tolerance: true, residual_abs_tol: 50, residual_rel_tol: 0.01 },
};

test("the Attribution tab carries its own portfolio input and renders the waterfall", async () => {
  const user = userEvent.setup();
  server.use(jsonGet("/api/attribution", ATTRIBUTION_AAA));
  renderWithClient(<BuildBasket />);

  await user.click(screen.getByRole("tab", { name: /attribution/i }));
  const portfolio = screen.getByLabelText("portfolio");
  await user.type(portfolio, "demo-book");
  await user.click(screen.getByRole("button", { name: /P&L attribution/i }));

  await waitFor(() =>
    expect(screen.getByLabelText(/P&L attribution waterfall/i)).toBeInTheDocument(),
  );
});

test("an attribution error surfaces a labelled alert on the Attribution tab", async () => {
  const user = userEvent.setup();
  server.use(
    http.get("/api/attribution", () =>
      HttpResponse.json({ error: "bad_attr", detail: "nope" }, { status: 400 }),
    ),
  );
  renderWithClient(<BuildBasket />);

  await user.click(screen.getByRole("tab", { name: /attribution/i }));
  await user.click(screen.getByRole("button", { name: /P&L attribution/i }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to load attribution/i),
  );
  expect(screen.getByRole("alert")).toHaveTextContent(/nope/);
});
