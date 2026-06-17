import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { RateDiagnostics } from "../api";
import { RateDiagnosticsPanel } from "./RateDiagnostics";

const DIAG: RateDiagnostics = {
  forward_price: 4812.5,
  implied_rate: 0.0254,
  implied_carry: -0.0131,
  implied_dividend: 0.0385,
  rate_unit: "/yr (annualized, continuous)",
};

test("renders the explicit interest rate r(T) with its annualized unit", () => {
  render(<RateDiagnosticsPanel diagnostics={DIAG} maturityLabel="3m (0.250y)" currency="€" />);
  const panel = screen.getByLabelText("Rate diagnostics");
  // r is shown as a percentage carrying the BFF's rate_unit — the explicit, displayed input.
  expect(within(panel).getByText(/2\.540% \/yr \(annualized, continuous\)/)).toBeInTheDocument();
});

test("renders the carry split q = r − ln(F/S)/T (carry + dividend), each with its unit", () => {
  render(<RateDiagnosticsPanel diagnostics={DIAG} maturityLabel="3m (0.250y)" currency="€" />);
  const panel = screen.getByLabelText("Rate diagnostics");
  expect(within(panel).getByText(/-1\.310% \/yr/)).toBeInTheDocument();
  expect(within(panel).getByText(/3\.850% \/yr/)).toBeInTheDocument();
});

test("renders the forward as a plain price in the index currency", () => {
  render(<RateDiagnosticsPanel diagnostics={DIAG} maturityLabel="3m (0.250y)" currency="€" />);
  const panel = screen.getByLabelText("Rate diagnostics");
  expect(within(panel).getByText(/4,812\.5 €/)).toBeInTheDocument();
});

test("a null field shows an honest dash, never a fabricated rate", () => {
  const partial: RateDiagnostics = {
    forward_price: 4812.5,
    implied_rate: 0.0254,
    implied_carry: null,
    implied_dividend: null,
    rate_unit: "/yr (annualized, continuous)",
  };
  render(<RateDiagnosticsPanel diagnostics={partial} maturityLabel="3m (0.250y)" />);
  const panel = screen.getByLabelText("Rate diagnostics");
  // Carry + dividend are null → two honest dashes; r still renders.
  expect(within(panel).getAllByText("-").length).toBe(2);
});

test("no diagnostics banked for the tenor reads as a labelled projection gap", () => {
  render(<RateDiagnosticsPanel diagnostics={null} maturityLabel="12m (1.000y)" />);
  const panel = screen.getByLabelText("Rate diagnostics");
  expect(within(panel).getByText(/No forward\/rate diagnostic banked/i)).toBeInTheDocument();
});
