import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("./components/Plot", async () => await import("./test/plotMock"));
vi.mock("./components/CandleChart", async () => await import("./test/candleMock"));
vi.mock("./components/LightweightLineChart", async () => await import("./test/lightweightLineMock"));

import { App } from "./App";
import { RECORDED_EMPTY } from "./test/fixtures";
import type { ScenariosResponse } from "./stressApi";

const PORTFOLIOS = { portfolios: ["CORE-INDEX-OPTIONS"] };

const SCENARIOS: ScenariosResponse = {
  portfolio_id: null,
  n_cells: 0,
  surface: {
    spot_shock: [],
    vol_shock: [],
    scenario_pnl: [],
    scenario_version: null,
    unit: "$ (full-reprice PnL)",
    n_cells: 0,
    has_holes: false,
    n_holes: 0,
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

function mockShellEndpoints(): void {
  const table: Record<string, unknown> = {
    "/api/recorded-dates": RECORDED_EMPTY,
    "/api/risk/portfolios": PORTFOLIOS,
    "/api/risk/scenarios": SCENARIOS,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const path = new URL(input, "http://localhost").pathname;
      const value = table[path];
      const ok = value !== undefined;
      return Promise.resolve({
        ok,
        status: ok ? 200 : 500,
        statusText: ok ? "OK" : "Server Error",
        json: async () => value ?? { error: "not mocked" },
      } as Response);
    }),
  );
}

test("top navigation reaches Market, Risk Scenarios, and Orders", async () => {
  mockShellEndpoints();
  const user = userEvent.setup();
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Market" })).toBeInTheDocument();

  await user.click(screen.getByRole("link", { name: "Risk Scenarios" }));
  expect(await screen.findByRole("heading", { name: "Risk Scenarios" })).toBeInTheDocument();
  await waitFor(() => expect(window.location.pathname).toBe("/risk"));

  await user.click(screen.getByRole("link", { name: "Orders" }));
  expect(await screen.findByRole("heading", { name: "Orders" })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/orders");
});

test("risk scenarios is directly addressable", async () => {
  mockShellEndpoints();
  window.history.pushState({}, "", "/risk");
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Risk Scenarios" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Risk Scenarios" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});
