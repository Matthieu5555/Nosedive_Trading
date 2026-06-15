// CDC buildout phases 2 + 3: the nappe heatmap (§3.4) and the ATM term structure (§3.5).
// Both read the dense reconstructed surface; the ATM cut falls back to the per-maturity smiles.
// The canvas wrappers are stubbed (jsdom has no canvas): the Plot stub exposes the heatmap z-grid,
// the LightweightLine stub exposes the plotted point count, series, and values — so a test can
// assert WHAT reached the chart (the cleaned grid, the ATM IVs), with values derived by hand from
// the fixtures, never copied from the component.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

vi.mock("./Plot", async () => await import("../test/plotMock"));
vi.mock("./LightweightLineChart", async () => await import("../test/lightweightLineMock"));

import { IV_SANE_MAX } from "../lib/volRobust";
import {
  ANALYTICS_AAA,
  ANALYTICS_AAA_DEGENERATE,
  ANALYTICS_AAA_DENSE,
} from "../test/fixtures";
import { AtmTermStructure, VolHeatmap } from "./charts";

describe("VolHeatmap (§3.4 nappe)", () => {
  test("plots a heatmap of the dense IV lattice, pinned to the sane colour band", () => {
    render(<VolHeatmap surface={ANALYTICS_AAA_DENSE.surface} />);
    const fig = screen.getByLabelText(/Implied-volatility nappe \(heatmap/i);
    expect(within(fig).getByTestId("plot-types")).toHaveTextContent("heatmap");
    // The z-grid is the dense lattice unchanged (every cell sane): 2 maturities × 3 log-moneyness.
    const z = JSON.parse(within(fig).getByTestId("plot-z").textContent || "[]") as (
      | number
      | null
    )[][];
    expect(z).toEqual([
      [0.27, 0.24, 0.25],
      [0.23, 0.21, 0.22],
    ]);
  });

  test("clamps a railed cell to a hole, collapses the duplicate column, and flags the slice", () => {
    render(<VolHeatmap surface={ANALYTICS_AAA_DEGENERATE.surface} />);
    const fig = screen.getByLabelText(/Implied-volatility nappe \(heatmap/i);
    expect(fig.getAttribute("aria-label")).toMatch(/1 slice flagged/i);
    const z = JSON.parse(within(fig).getByTestId("plot-z").textContent || "[]") as (
      | number
      | null
    )[][];
    // 5 raw columns with a duplicated -0.1 collapse to 4; the railed 1.4 cell is a null hole.
    expect(z[0].length).toBe(4);
    expect(z[0][0]).toBeNull();
    const finite = z.flat().filter((v): v is number => typeof v === "number");
    expect(Math.max(...finite)).toBeLessThanOrEqual(IV_SANE_MAX);
  });

  test("shows an honest empty state when no surface was reconstructed", () => {
    render(<VolHeatmap surface={null} />);
    const fig = screen.getByLabelText(/Implied-volatility nappe \(heatmap/i);
    expect(fig).toHaveTextContent(/No reconstructed surface/i);
  });
});

describe("AtmTermStructure (§3.5)", () => {
  test("plots the at-the-money IV (k≈0 column) per maturity off the dense lattice", () => {
    render(
      <AtmTermStructure
        surface={ANALYTICS_AAA_DENSE.surface}
        maturities={ANALYTICS_AAA_DENSE.maturities}
      />,
    );
    const fig = screen.getByLabelText(/ATM term structure/i);
    expect(within(fig).getByTestId("line-series")).toHaveTextContent("ATM IV");
    expect(within(fig).getByTestId("line-unit")).toHaveTextContent("IV");
    // Column nearest log-moneyness 0 is index 1 (k=0.0): ATM IV is 0.24 at 0.25y and 0.21 at 1.0y.
    const values = JSON.parse(
      within(fig).getByTestId("line-values").textContent || "[]",
    ) as number[][];
    expect(values).toEqual([[0.24, 0.21]]);
  });

  test("excludes a railed ATM cell's slice from the flag count but keeps the sane ATM points", () => {
    render(
      <AtmTermStructure
        surface={ANALYTICS_AAA_DEGENERATE.surface}
        maturities={ANALYTICS_AAA_DEGENERATE.maturities}
      />,
    );
    const fig = screen.getByLabelText(/ATM term structure/i);
    expect(fig.getAttribute("aria-label")).toMatch(/1 slice flagged/i);
    // ATM column (k=0.0) is sane on both rows: 0.15 at the short slice, 0.21 at 1.0y.
    const values = JSON.parse(
      within(fig).getByTestId("line-values").textContent || "[]",
    ) as number[][];
    expect(values).toEqual([[0.15, 0.21]]);
  });

  test("falls back to the per-maturity smile's k≈0 point when no dense surface exists", () => {
    render(
      <AtmTermStructure surface={ANALYTICS_AAA.surface} maturities={ANALYTICS_AAA.maturities} />,
    );
    const fig = screen.getByLabelText(/ATM term structure/i);
    // ANALYTICS_AAA has no dense surface; its one maturity's smile k = [-0.15, 0.12]; the nearer to
    // 0 is 0.12 → IV 0.23.
    const values = JSON.parse(
      within(fig).getByTestId("line-values").textContent || "[]",
    ) as number[][];
    expect(values).toEqual([[0.23]]);
  });

  test("shows an honest empty state when there is nothing to plot", () => {
    render(<AtmTermStructure surface={null} maturities={[]} />);
    const fig = screen.getByLabelText(/ATM term structure/i);
    expect(fig).toHaveTextContent(/No ATM term structure/i);
  });
});
