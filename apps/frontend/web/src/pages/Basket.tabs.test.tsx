import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import type { AttributionResponse } from "../api";
import { BASKET_RISK_AAA } from "../test/fixtures";
import { jsonGet, jsonPost, server } from "../test/server";
import { BasketPage } from "./Basket";

test("the page splits into the four Basket blocks (compose → book → shock → explain), Compose first", () => {
  render(<BasketPage />);

  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((tab) => tab.textContent)).toEqual([
    "① Compose",
    "② The Book",
    "③ Stress",
    "④ Attribution",
  ]);
  expect(screen.getByRole("tab", { name: /compose/i })).toHaveAttribute("data-state", "active");
});

test("the leg composer (templates + grid + controls) is shared above the tabs", async () => {
  const user = userEvent.setup();
  render(<BasketPage />);

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
  render(<BasketPage />);

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
  render(<BasketPage />);

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
  render(<BasketPage />);

  await user.click(screen.getByRole("tab", { name: /attribution/i }));
  await user.click(screen.getByRole("button", { name: /P&L attribution/i }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to load attribution/i),
  );
  expect(screen.getByRole("alert")).toHaveTextContent(/nope/);
});
