import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import { RiskScenariosPage } from "./RiskScenarios";
import type { ScenariosResponse } from "../stressApi";

const PORTFOLIOS = { portfolios: ["CORE-INDEX-OPTIONS"] };

const SCENARIOS: ScenariosResponse = {
  portfolio_id: null,
  n_cells: 9,
  surface: {
    spot_shock: [-0.5, 0, 0.5],
    vol_shock: [-0.1, 0, 0.1],
    scenario_pnl: [
      [-1200, -800, -300],
      [-200, 0, 250],
      [400, 900, 1500],
    ],
    scenario_version: "v3",
    unit: "$ (full-reprice PnL)",
    n_cells: 9,
  },
};

const SCENARIOS_EMPTY: ScenariosResponse = {
  portfolio_id: null,
  n_cells: 0,
  surface: {
    spot_shock: [],
    vol_shock: [],
    scenario_pnl: [],
    scenario_version: null,
    unit: "$ (full-reprice PnL)",
    n_cells: 0,
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockEndpoints(scenarios: unknown = SCENARIOS): void {
  const table: Record<string, unknown> = {
    "/api/risk/portfolios": PORTFOLIOS,
    "/api/risk/scenarios": scenarios,
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

test("renders the stress summary with max gain/loss and a portfolio selector", async () => {
  mockEndpoints();
  render(<RiskScenariosPage />);
  expect(await screen.findByText("Stress summary")).toBeInTheDocument();
  // Max gain 1500, max loss -1200 (signed money, no decimals).
  expect(screen.getByText("+$1,500")).toBeInTheDocument();
  expect(screen.getByText("-$1,200")).toBeInTheDocument();
  expect(await screen.findByLabelText("Portfolio")).toBeInTheDocument();
});

test("renders the PnL surface and heatmap as Plotly traces", async () => {
  mockEndpoints();
  render(<RiskScenariosPage />);
  const surface = await screen.findByLabelText(/Stress PnL surface/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("surface");
  const heatmap = await screen.findByLabelText(/Stress PnL heatmap/i);
  expect(within(heatmap).getByTestId("plot-types")).toHaveTextContent("heatmap");
});

test("renders a labeled empty state when no surface is persisted", async () => {
  mockEndpoints(SCENARIOS_EMPTY);
  render(<RiskScenariosPage />);
  expect(await screen.findByText(/No stress surface persisted yet/i)).toBeInTheDocument();
});

test("a fetch error renders through AsyncBlock, not a blank page", async () => {
  // Only portfolios is mocked; /api/risk/scenarios is absent → 500 → error path.
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const path = new URL(input, "http://localhost").pathname;
      const value = path === "/api/risk/portfolios" ? PORTFOLIOS : undefined;
      const ok = value !== undefined;
      return Promise.resolve({
        ok,
        status: ok ? 200 : 500,
        statusText: ok ? "OK" : "Server Error",
        json: async () => value ?? { error: "not mocked" },
      } as Response);
    }),
  );
  render(<RiskScenariosPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/error|failed|500/i);
  });
});
