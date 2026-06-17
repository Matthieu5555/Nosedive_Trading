import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";

vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import type { NamedScenario, RateScenario, ScenariosResponse } from "../stressApi";
import { renderWithClient } from "../test/renderWithClient";
import { jsonGet, notMocked, server } from "../test/server";
import { RiskScenariosPage } from "./RiskScenarios";

const NAMED: NamedScenario[] = [
  {
    scenario_id: "named_2008",
    label: "2008",
    spot_shock: -0.4,
    vol_shock: 0.3,
    rate_shock: -0.01,
    scenario_pnl: -2000,
    scenario_version: "v3",
    n_legs: 2,
    unit: "$ (full-reprice PnL)",
  },
];

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
  named: NAMED,
  n_named: 1,
};

const RATE: RateScenario[] = [
  {
    scenario_id: "rate_+0.0010",
    rate_shock: 0.001,
    bp: 10,
    scenario_pnl: 480,
    scenario_version: "v3",
    n_legs: 2,
    unit: "$ (full-reprice PnL)",
    bp_unit: "bp",
  },
  {
    scenario_id: "rate_-0.0010",
    rate_shock: -0.001,
    bp: -10,
    scenario_pnl: -450,
    scenario_version: "v3",
    n_legs: 2,
    unit: "$ (full-reprice PnL)",
    bp_unit: "bp",
  },
];

const SCENARIOS_WITH_RATE: ScenariosResponse = {
  ...SCENARIOS,
  rate: RATE,
  n_rate: RATE.length,
};

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

  expect(screen.getByText(/8 cells, 1 missing/)).toBeInTheDocument();

  expect(screen.getByText("1.5 × 10³ $ (full-reprice PnL)")).toBeInTheDocument();
  expect(screen.getByText("-1.2 × 10³ $ (full-reprice PnL)")).toBeInTheDocument();
});

test("renders a labeled empty state when no surface is persisted", async () => {
  renderWithClient(<RiskScenariosPage />);
  expect(await screen.findByText(/No stress surface persisted yet/i)).toBeInTheDocument();
});

test("a fetch error renders through AsyncBlock, not a blank page", async () => {
  server.use(http.get("/api/risk/scenarios", notMocked));
  renderWithClient(<RiskScenariosPage />);
  await waitFor(() => {
    const alerts = screen.getAllByRole("alert");
    expect(alerts.length).toBeGreaterThan(0);
    expect(alerts[0]).toHaveTextContent(/error|failed|500/i);
  });
});

test("named historical scenarios surface their label and stressed P&L", async () => {
  server.use(jsonGet("/api/risk/scenarios", SCENARIOS));
  renderWithClient(<RiskScenariosPage />);

  expect(await screen.findByText(/Worst case: 2008/)).toBeInTheDocument();
  expect(screen.getAllByText("-2 × 10³ $ (full-reprice PnL)").length).toBeGreaterThan(0);
});

test("the rate-shock sweep renders beside the surface when the grid carries a rate family", async () => {
  server.use(jsonGet("/api/risk/scenarios", SCENARIOS_WITH_RATE));
  renderWithClient(<RiskScenariosPage />);

  expect(await screen.findByRole("heading", { name: "Rate-shock sweep" })).toBeInTheDocument();
  const table = await screen.findByRole("table", { name: /Rate-shock sweep/i });
  expect(within(table).getByText("-1 × 10¹ bp")).toBeInTheDocument();
  expect(within(table).getByText("1 × 10¹ bp")).toBeInTheDocument();
});

test("no rate family means no rate panel, the surface render stays as it was", async () => {
  server.use(jsonGet("/api/risk/scenarios", SCENARIOS));
  renderWithClient(<RiskScenariosPage />);

  expect(await screen.findByText("Stress summary")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Rate-shock sweep" })).not.toBeInTheDocument();
});

test("the broker reconciliation panel reads back the default agreeing snapshot", async () => {
  renderWithClient(<RiskScenariosPage />);
  expect(await screen.findByText(/Does the broker agree with our book/i)).toBeInTheDocument();
  expect(await screen.findByText("In agreement")).toBeInTheDocument();
});

test("no broker account captured is a plain empty state, not an error alert", async () => {
  server.use(
    http.get("/api/reconciliation", () =>
      HttpResponse.json({ error: "no_broker_account", detail: "none" }, { status: 400 }),
    ),
  );
  renderWithClient(<RiskScenariosPage />);
  expect(
    await screen.findByText(/No broker account snapshot has been captured yet/i),
  ).toBeInTheDocument();
});

test("the book P&L attribution panel renders its labelled-empty state by default", async () => {
  renderWithClient(<RiskScenariosPage />);
  expect(await screen.findByText("Where the P&L came from")).toBeInTheDocument();
  expect(await screen.findByText(/No P&L attribution for this selection yet/i)).toBeInTheDocument();
});
