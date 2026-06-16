import { render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

vi.mock("./Plot", async () => await import("../test/plotMock"));
vi.mock("./LightweightLineChart", async () => await import("../test/lightweightLineMock"));

import { IV_SANE_MAX } from "../lib/volRobust";
import { ANALYTICS_AAA, ANALYTICS_AAA_DEGENERATE, ANALYTICS_AAA_DENSE } from "../test/fixtures";
import { AtmTermStructure, VolHeatmap } from "./charts";

describe("VolHeatmap (§3.4 nappe)", () => {
  test("plots a heatmap of the dense IV lattice, pinned to the sane colour band", () => {
    render(<VolHeatmap surface={ANALYTICS_AAA_DENSE.surface} />);
    const fig = screen.getByLabelText(/Implied-volatility nappe \(heatmap/i);
    expect(within(fig).getByTestId("plot-types")).toHaveTextContent("heatmap");

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
