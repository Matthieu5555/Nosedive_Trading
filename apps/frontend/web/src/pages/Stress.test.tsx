import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

// Plotly draws to a canvas jsdom does not implement; swap the wrapper for the DOM stub that
// exposes the trace types and the self-label as text (see src/test/plotMock.tsx).
vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import { StressPage } from "./Stress";
import { mockFetch } from "../test/http";
import type { ScenariosResponse } from "../stressApi";

afterEach(() => {
  vi.unstubAllGlobals();
});

// A 3×3 surface with a hand-built z-grid; the centre cell is 0 by construction.
const SURFACE_PAYLOAD: ScenariosResponse = {
  portfolio_id: "pf-surface",
  n_cells: 9,
  surface: {
    spot_shock: [-0.5, 0.0, 0.5],
    vol_shock: [-0.5, 0.0, 0.5],
    scenario_pnl: [
      [-5000, -4000, -3000],
      [-100, 0, 150],
      [3000, 4000, 5000],
    ],
    scenario_version: "scn-1+grid+surf",
    unit: "$ (full-reprice PnL)",
    n_cells: 9,
  },
};

const EMPTY_PAYLOAD: ScenariosResponse = {
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

test("renders the Plotly surface trace from the scenarios payload", async () => {
  mockFetch(SURFACE_PAYLOAD);
  render(<StressPage />);
  // The surface trace was requested (the plot mock exposes trace types as text).
  expect(await screen.findByTestId("plot-types")).toHaveTextContent("surface");
  // The panel self-labels the shock conventions in visible text (the explanatory sentence,
  // distinct from the chart's own label which also names them).
  expect(screen.getByText(/Spot shock is relative/)).toBeInTheDocument();
  expect(screen.getByText(/vol shock is additive/)).toBeInTheDocument();
});

test("shows the PnL unit string", async () => {
  mockFetch(SURFACE_PAYLOAD);
  render(<StressPage />);
  expect(await screen.findByText(/PnL unit:/)).toBeInTheDocument();
  expect(screen.getByText("$ (full-reprice PnL)")).toBeInTheDocument();
});

test("renders an empty state when no surface is persisted", async () => {
  mockFetch(EMPTY_PAYLOAD);
  render(<StressPage />);
  expect(await screen.findByText(/No stress surface/)).toBeInTheDocument();
});

test("renders a typed error, not a blank page, when the API fails", async () => {
  mockFetch({ error: "boom" }, false);
  render(<StressPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent("Failed to load");
  });
});
