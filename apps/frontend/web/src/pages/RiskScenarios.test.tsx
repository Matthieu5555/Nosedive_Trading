import { screen, waitFor, within } from "@testing-library/react";
import { http } from "msw";
import { expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import type { ScenariosResponse } from "../stressApi";
import { renderWithClient } from "../test/renderWithClient";
import { jsonGet, notMocked, server } from "../test/server";
import { RiskScenariosPage } from "./RiskScenarios";

// /api/risk/portfolios is served by the msw defaults; each test picks its scenarios payload.

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
    has_holes: false,
    n_holes: 0,
  },
};

// One (spot, vol) combination genuinely missing: a labelled null hole (F-BFF-03), never 0.0.
// The hole at [0][2] would have been the max gain if zero-filled stats coerced it.
const SCENARIOS_WITH_HOLE: ScenariosResponse = {
  portfolio_id: null,
  n_cells: 8,
  surface: {
    spot_shock: [-0.5, 0, 0.5],
    vol_shock: [-0.1, 0, 0.1],
    scenario_pnl: [
      [-1200, -800, null],
      [-200, 0, 250],
      [400, 900, 1500],
    ],
    scenario_version: "v3",
    unit: "$ (full-reprice PnL)",
    n_cells: 8,
    has_holes: true,
    n_holes: 1,
  },
};

test("renders the stress summary with max gain/loss and a portfolio selector", async () => {
  server.use(jsonGet("/api/risk/scenarios", SCENARIOS));
  renderWithClient(<RiskScenariosPage />);
  expect(await screen.findByText("Stress summary")).toBeInTheDocument();
  // Max gain 1500, max loss -1200, rendered in scientific notation (six sig figs, trailing
  // zeros stripped) with the backend PnL unit string adjacent (owner ruling 2026-06-15):
  //   1500  → "1.5 × 10³"  (1.50000e+3 → mantissa 1.5, exp 3)
  //   -1200 → "-1.2 × 10³" (-1.20000e+3 → mantissa -1.2, exp 3)
  expect(screen.getByText("1.5 × 10³ $ (full-reprice PnL)")).toBeInTheDocument();
  expect(screen.getByText("-1.2 × 10³ $ (full-reprice PnL)")).toBeInTheDocument();
  expect(await screen.findByLabelText("Portfolio")).toBeInTheDocument();
});

test("renders the PnL surface and heatmap as Plotly traces", async () => {
  server.use(jsonGet("/api/risk/scenarios", SCENARIOS));
  renderWithClient(<RiskScenariosPage />);
  const surface = await screen.findByLabelText(/Stress PnL surface/i);
  expect(within(surface).getByTestId("plot-types")).toHaveTextContent("surface");
  const heatmap = await screen.findByLabelText(/Stress PnL heatmap/i);
  expect(within(heatmap).getByTestId("plot-types")).toHaveTextContent("heatmap");
});

test("a missing cell is reported as missing and excluded from the gain/loss stats", async () => {
  server.use(jsonGet("/api/risk/scenarios", SCENARIOS_WITH_HOLE));
  renderWithClient(<RiskScenariosPage />);
  expect(await screen.findByText("Stress summary")).toBeInTheDocument();
  // The hole is announced beside the cell count…
  expect(screen.getByText(/8 cells — 1 missing/)).toBeInTheDocument();
  // …and the stats come from the real cells only: max gain is 1500 (the hole is not a 0
  // and not a fabricated extreme), max loss is -1200 — scientific notation + PnL unit.
  expect(screen.getByText("1.5 × 10³ $ (full-reprice PnL)")).toBeInTheDocument();
  expect(screen.getByText("-1.2 × 10³ $ (full-reprice PnL)")).toBeInTheDocument();
});

test("renders a labeled empty state when no surface is persisted", async () => {
  // The msw default for /api/risk/scenarios IS the empty surface (SCENARIOS_EMPTY).
  renderWithClient(<RiskScenariosPage />);
  expect(await screen.findByText(/No stress surface persisted yet/i)).toBeInTheDocument();
});

test("a fetch error renders through AsyncBlock, not a blank page", async () => {
  // Portfolios stays on its default; /api/risk/scenarios is forced onto the 500 path.
  server.use(http.get("/api/risk/scenarios", notMocked));
  renderWithClient(<RiskScenariosPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/error|failed|500/i);
  });
});
