import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { OrdersPage } from "./Orders";

test("orders tab is a read-only execution sketch", () => {
  render(<OrdersPage />);
  // The sketch banner makes the read-only intent explicit.
  expect(screen.getByText(/Execution sketch — read-only/i)).toBeInTheDocument();
  // Submit is disabled (no broker wiring).
  expect(screen.getByRole("button", { name: /Submit/i })).toBeDisabled();
});

test("the indicative preview reflects the default ticket notional", () => {
  render(<OrdersPage />);
  // Default ticket: qty 2 × limit 47.5 × 100 multiplier = $9,500.
  expect(screen.getByText("$9,500")).toBeInTheDocument();
});
