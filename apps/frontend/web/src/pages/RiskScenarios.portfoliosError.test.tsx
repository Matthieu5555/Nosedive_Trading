import { screen } from "@testing-library/react";
import { http } from "msw";
import { expect, test, vi } from "vitest";

// Plotly draws to a canvas jsdom does not implement; swap the wrapper for the DOM stub.
vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import { renderWithClient } from "../test/renderWithClient";
import { notMocked, server } from "../test/server";
import { RiskScenariosPage } from "./RiskScenarios";

// The portfolio dropdown is fed by a TanStack query; its failure was swallowed (the select just
// showed "All portfolios" with no hint the real list never loaded). It must now surface.

test("a failing /api/risk/portfolios is surfaced beside the (still-disabled) selector", async () => {
  server.use(http.get("/api/risk/portfolios", () => notMocked()));

  renderWithClient(<RiskScenariosPage />);

  expect(await screen.findByText(/Could not load the portfolio list/)).toBeInTheDocument();
});
