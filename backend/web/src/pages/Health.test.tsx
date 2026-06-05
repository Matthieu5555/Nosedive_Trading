import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { HealthPage } from "./Health";
import { HEALTH_DEGRADED, HEALTH_HEALTHY } from "../test/fixtures";
import { mockFetch } from "../test/http";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("reports a healthy system with all flags in their good state", async () => {
  mockFetch(HEALTH_HEALTHY);
  render(<HealthPage />);

  expect(await screen.findByText("healthy")).toBeInTheDocument();
  expect(screen.getByText("passing")).toBeInTheDocument();
  expect(screen.getByText("current")).toBeInTheDocument();
});

test("reports a degraded system and lists the backlog", async () => {
  mockFetch(HEALTH_DEGRADED);
  render(<HealthPage />);

  expect(await screen.findByText("attention needed")).toBeInTheDocument();
  expect(screen.getByText("no_data")).toBeInTheDocument();
  expect(screen.getByText(/Backlog: analytics, qc/)).toBeInTheDocument();
});
