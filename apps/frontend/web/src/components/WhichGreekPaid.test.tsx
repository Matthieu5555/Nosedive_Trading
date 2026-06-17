import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import type { BacktestAttribution } from "../api";

// Capture the data handed to Plot so we can assert the bar marker colors come from the shared
// sign-color tokens, not a one-off hex pair.
const captured: { data?: unknown } = {};
vi.mock("./Plot", () => ({
  Plot: ({ data, label }: { data: unknown; label: string }) => {
    captured.data = data;
    return <figure aria-label={label} />;
  },
}));

// Pin the token resolver so the test asserts against known values rather than whatever the live
// stylesheet computes (jsdom has no CSS custom properties resolved).
vi.mock("../lib/tokens", () => ({
  token: (name: string) => (name === "positive" ? "rgb(0, 200, 0)" : "rgb(220, 0, 0)"),
}));

import { WhichGreekPaid } from "./WhichGreekPaid";

const ATTRIBUTION: BacktestAttribution = {
  delta: 100,
  gamma: -40,
  vega: 25,
  theta: 250,
  rho: -5,
  vanna: 3,
  volga: -2,
};

function markerColors(): string[] {
  const traces = captured.data as Array<{ marker: { color: string[] } }>;
  return traces[0].marker.color;
}

test("bar marker colors come from the shared sign tokens, not hardcoded hex", () => {
  render(<WhichGreekPaid attribution={ATTRIBUTION} currency="EUR" kicker="test" />);

  // Greek order: delta, gamma, vega, theta, rho, vanna, volga.
  // Signs:        +      -      +     +      -     +      -
  const colors = markerColors();
  expect(colors).toEqual([
    "rgb(0, 200, 0)", // delta +  → positive token
    "rgb(220, 0, 0)", // gamma -  → negative token
    "rgb(0, 200, 0)", // vega  +  → positive token
    "rgb(0, 200, 0)", // theta +  → positive token
    "rgb(220, 0, 0)", // rho   -  → negative token
    "rgb(0, 200, 0)", // vanna +  → positive token
    "rgb(220, 0, 0)", // volga -  → negative token
  ]);

  // No legacy one-off hex leaks through.
  expect(colors).not.toContain("#c0392b");
  expect(colors).not.toContain("#1e7e4f");
});

test("renders the panel with its heading and the leader callout", () => {
  render(<WhichGreekPaid attribution={ATTRIBUTION} currency="EUR" kicker="test" />);

  expect(screen.getByRole("heading", { name: "Where the return came from" })).toBeInTheDocument();
  // theta (250) is the largest absolute contribution and is positive → "paid most".
  expect(screen.getByText("Theta paid most")).toBeInTheDocument();
});
