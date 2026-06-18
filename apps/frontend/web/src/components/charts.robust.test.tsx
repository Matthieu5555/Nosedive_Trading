import { render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

vi.mock("./Plot", async () => await import("../test/plotMock"));
vi.mock("./LightweightLineChart", async () => await import("../test/lightweightLineMock"));

import type { AnalyticsMaturity } from "../api";
import { IV_SANE_MAX } from "../lib/volRobust";
import {
  ANALYTICS_AAA_DEGENERATE,
  SURFACE_DENSE_FILLED_AND_HOLEY,
  SURFACE_DENSE_THREE_ROWS,
} from "../test/fixtures";
import { floorSliceDenseSurface, SmileChart, VolSurface } from "./charts";

const DEGEN = ANALYTICS_AAA_DEGENERATE;
const DEGEN_MATURITY: AnalyticsMaturity = DEGEN.maturities[0];

describe("VolSurface dense surface robustness", () => {
  test("clamps out-of-band cells to null, collapses duplicate columns, and flags the slice", () => {
    render(
      <VolSurface
        subject={DEGEN.underlying}
        surface={DEGEN.surface}
        maturities={DEGEN.maturities}
      />,
    );

    const fig = screen.getByLabelText(/Volatility surface, AAA/i);
    expect(fig.getAttribute("aria-label")).toMatch(/1 slice flagged/i);

    const z = JSON.parse(within(fig).getByTestId("plot-z").textContent || "[]") as (
      | number
      | null
    )[][];
    expect(z[0].length).toBe(4);
    const finite = z.flat().filter((v): v is number => typeof v === "number");
    expect(Math.max(...finite)).toBeLessThanOrEqual(IV_SANE_MAX);
    expect(z[0]).toContain(null);
  });
});

describe("SmileChart robustness", () => {
  test("drops absurd/NaN/duplicate points and notes the count, plotting both cleaned wings", () => {
    // Pin to the single tenor; the degenerate-fit flag and the dropped-point note ride the label.
    render(
      <SmileChart
        subject={DEGEN.underlying}
        maturities={[DEGEN_MATURITY]}
        maturityLabel={DEGEN_MATURITY.label}
      />,
    );
    const fig = screen.getByLabelText(/smile 10d/i);

    expect(fig.getAttribute("aria-label")).toMatch(/degenerate fit/i);
    expect(fig.getAttribute("aria-label")).toMatch(/flagged/i);

    // The smile is a Plotly scatter on a real log-moneyness axis; both cleaned wings (ATM shared)
    // contribute their points (put + call superimposed, no side filter).
    expect(within(fig).getByTestId("plot-types").textContent).toMatch(/scatter/);
    const plotted = Number(within(fig).getByTestId("plot-points").textContent);
    expect(plotted).toBe(4);
  });
});

// A maturity whose captured cells span BOTH sides of ATM (low strikes k<0 and high strikes k>0),
// the real per-side shape: every strike carries that side's own quoted IV.
const TWO_WING_MATURITY: AnalyticsMaturity = {
  maturity_years: 0.027,
  tenor_label: "10d",
  label: "10d (0.027y)",
  smile: {
    axis_type: "delta",
    deltas: [-0.1, -0.02, 0.0, 0.02, 0.1],
    implied_vols: [0.21, 0.18, 0.14, 0.16, 0.2],
    log_moneyness: [-0.07, -0.03, 0.0, 0.03, 0.07],
  },
  surface_slice: null,
  points: [],
};

describe("SmileChart side semantics (deep-OTM-put correctness)", () => {
  test("combined splits the curve into a put wing and a call wing", () => {
    render(
      <SmileChart
        subject="SX5E"
        maturities={[TWO_WING_MATURITY]}
        maturityLabel={TWO_WING_MATURITY.label}
        side="combined"
      />,
    );
    const fig = screen.getByLabelText(/smile 10d/i);
    // Two scatter traces: puts (k <= 0) and calls (k >= 0).
    expect(within(fig).getByTestId("plot-types").textContent).toBe("scatter,scatter");
    expect(fig.getAttribute("aria-label")).toMatch(/puts ◄ ATM ► calls/i);
  });

  test("a put-side smile is ONE continuous put-quoted curve, never split as if calls", () => {
    render(
      <SmileChart
        subject="SX5E"
        maturities={[TWO_WING_MATURITY]}
        maturityLabel={TWO_WING_MATURITY.label}
        side="put"
      />,
    );
    const fig = screen.getByLabelText(/smile 10d/i);
    // One scatter trace (the whole curve is puts), every captured point in it.
    expect(within(fig).getByTestId("plot-types").textContent).toBe("scatter");
    expect(Number(within(fig).getByTestId("plot-points").textContent)).toBe(5);
    // Labelled as put-quoted, deep-OTM puts on the low-strike (left) wing, not "puts ◄ ATM ► calls".
    expect(fig.getAttribute("aria-label")).toMatch(/put-quoted/i);
    expect(fig.getAttribute("aria-label")).not.toMatch(/ATM ► calls/i);
  });
});

// BUG #3: the maturity-floor control used to blank the 3D surface (it nulled the dense grid for any
// non-zero floor, dropping into a coarse fallback that could leave <2 z-rows, and Plotly `surface`
// needs ≥2 rows to draw). The fix slices the SAME dense grid to rows ≥ floor, clamping so ≥2 rows
// always remain. These tests pin the pure slice helper and the rendered (never-blank) outcome.
describe("floorSliceDenseSurface (maturity-floor slice of the dense grid)", () => {
  // Three rows at 0.083y (1m), 0.5y (6m), 1.0y (1y); x is the untouched 3-column log-moneyness grid.
  const dense = SURFACE_DENSE_THREE_ROWS;

  test("no floor (0) passes the grid through unchanged", () => {
    const r = floorSliceDenseSurface(dense, 0);
    expect(r.surface).toBe(dense);
    expect(r.relaxed).toBe(false);
    expect(r.nDropped).toBe(0);
  });

  test("a floor drops the short-end rows below it, keeping x untouched", () => {
    // Floor at 0.5y keeps {0.5, 1.0}, drops 0.083 (1m).
    const r = floorSliceDenseSurface(dense, 0.5);
    expect(r.surface.maturity_years).toEqual([0.5, 1.0]);
    expect(r.surface.implied_vol).toEqual([
      [0.27, 0.2, 0.24],
      [0.25, 0.19, 0.23],
    ]);
    // x (log-moneyness) is carried through untouched.
    expect(r.surface.log_moneyness).toEqual(dense.log_moneyness);
    expect(r.relaxed).toBe(false);
    expect(r.nDropped).toBe(1);
    expect(r.appliedFloorYears).toBe(0.5);
  });

  test("GUARD: a floor that would leave <2 rows is relaxed to keep the highest two", () => {
    // Floor at 1.0y would keep only {1.0} (one row, which Plotly can't draw). Clamp to the top two.
    const r = floorSliceDenseSurface(dense, 1.0);
    expect(r.surface.maturity_years).toEqual([0.5, 1.0]);
    expect(r.surface.maturity_years.length).toBeGreaterThanOrEqual(2);
    expect(r.relaxed).toBe(true);
    expect(r.appliedFloorYears).toBe(0.5);
  });

  test("never mutates the served grid", () => {
    const before = JSON.stringify(dense);
    floorSliceDenseSurface(dense, 0.5);
    floorSliceDenseSurface(dense, 1.0);
    expect(JSON.stringify(dense)).toBe(before);
  });

  test("carries only the surviving degenerate-maturity flags", () => {
    const flagged = { ...dense, degenerate_maturity_years: [0.083, 1.0] };
    const r = floorSliceDenseSurface(flagged, 0.5);
    // 0.083 was trimmed, so only 1.0 remains in the degenerate list.
    expect(r.surface.degenerate_maturity_years).toEqual([1.0]);
  });
});

describe("VolSurface maturity floor (never blanks, drops short tenors) — BUG #3", () => {
  test("a dense surface with a floor keeps ≥2 maturity rows and drops the rows below the floor", () => {
    render(
      <VolSurface
        subject="SX5E"
        surface={SURFACE_DENSE_THREE_ROWS}
        floorYears={0.5}
        maturities={[]}
      />,
    );
    const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
    const z = JSON.parse(within(fig).getByTestId("plot-z").textContent || "[]") as (
      | number
      | null
    )[][];
    // The z-grid has one row per surviving maturity: floor 0.5 keeps {0.5, 1.0} = 2 rows, never blank.
    expect(z.length).toBe(2);
    expect(z.length).toBeGreaterThanOrEqual(2);
    // The dropped short-end row (1m: [0.3, 0.22, 0.26]) is gone; the kept rows are the longer tenors.
    expect(z).toEqual([
      [0.27, 0.2, 0.24],
      [0.25, 0.19, 0.23],
    ]);
  });

  test("a floor that would leave one row is relaxed, not blanked, with an honest inline note", () => {
    render(
      <VolSurface
        subject="SX5E"
        surface={SURFACE_DENSE_THREE_ROWS}
        floorYears={1.0}
        maturities={[]}
      />,
    );
    // The relaxed note is surfaced as an inline status (no silent failure), and it rides the figure
    // label too. The text appears both in the status paragraph and the figcaption, so target the
    // status role for the inline note.
    const note = screen.getByRole("status");
    expect(note.textContent).toMatch(/Maturity floor eased/i);
    const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
    expect(fig.getAttribute("aria-label")).toMatch(/Maturity floor eased/i);
    const z = JSON.parse(within(fig).getByTestId("plot-z").textContent || "[]") as (
      | number
      | null
    )[][];
    // Clamped to the top two rows so the surface still draws.
    expect(z.length).toBe(2);
  });

  test("no floor (0) renders the full dense grid (all three rows)", () => {
    render(
      <VolSurface
        subject="SX5E"
        surface={SURFACE_DENSE_THREE_ROWS}
        floorYears={0}
        maturities={[]}
      />,
    );
    const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
    const z = JSON.parse(within(fig).getByTestId("plot-z").textContent || "[]") as (
      | number
      | null
    )[][];
    expect(z.length).toBe(3);
    expect(screen.queryByText(/Maturity floor eased/i)).toBeNull();
  });
});

// The clean/raw toggle (the `filled` prop) controls how interpolated the 3D surface looks. CLEAN
// (filled=true) renders the fully-filled implied_vol_filled grid (no holes, the classic smooth
// nappe); RAW (filled=false) renders the clamped implied_vol grid, which keeps its holes where the
// strikes stop. Toggling changes only the fill, never the maturity set, and the maturity floor still
// slices rows in both modes.
function readZ(): (number | null)[][] {
  const fig = screen.getByLabelText(/Volatility surface, SX5E/i);
  return JSON.parse(within(fig).getByTestId("plot-z").textContent || "[]") as (number | null)[][];
}

describe("VolSurface clean/raw fill toggle (filled prop)", () => {
  const dense = SURFACE_DENSE_FILLED_AND_HOLEY;

  test("filled=true renders the fully filled grid with NO null holes", () => {
    render(<VolSurface subject="SX5E" surface={dense} filled={true} maturities={[]} />);
    const z = readZ();
    // No cell is null: the smooth nappe is fully filled.
    expect(z.flat().some((v) => v === null)).toBe(false);
    // It is the filled grid (the interpolated deep-OTM-put column), not the holey one.
    expect(z).toEqual([
      [0.3, 0.22, 0.26],
      [0.28, 0.2, 0.24],
      [0.26, 0.19, 0.23],
    ]);
  });

  test("filled=false preserves the holes where strikes stop", () => {
    render(<VolSurface subject="SX5E" surface={dense} filled={false} maturities={[]} />);
    const z = readZ();
    // The clamped grid keeps its holes (the deep-OTM-put wing on the two longer tenors).
    expect(z.flat().filter((v) => v === null).length).toBe(2);
    expect(z).toEqual([
      [0.3, 0.22, 0.26],
      [null, 0.2, 0.24],
      [null, 0.19, 0.23],
    ]);
  });

  test("toggling fill does not change the maturity set (same rows, same y axis)", () => {
    const { rerender } = render(
      <VolSurface subject="SX5E" surface={dense} filled={true} maturities={[]} />,
    );
    const cleanRows = readZ().length;
    rerender(<VolSurface subject="SX5E" surface={dense} filled={false} maturities={[]} />);
    const rawRows = readZ().length;
    expect(cleanRows).toBe(3);
    expect(rawRows).toBe(3);
  });

  test("the maturity floor still slices rows in BOTH fill modes, on the same axes", () => {
    // Floor 0.5y keeps {0.5, 1.0} = 2 rows. Filled keeps the interpolated cells; raw keeps the holes.
    const { rerender } = render(
      <VolSurface subject="SX5E" surface={dense} filled={true} floorYears={0.5} maturities={[]} />,
    );
    expect(readZ()).toEqual([
      [0.28, 0.2, 0.24],
      [0.26, 0.19, 0.23],
    ]);
    rerender(
      <VolSurface subject="SX5E" surface={dense} filled={false} floorYears={0.5} maturities={[]} />,
    );
    expect(readZ()).toEqual([
      [null, 0.2, 0.24],
      [null, 0.19, 0.23],
    ]);
  });

  test("filled=true falls back to the clamped grid when the payload has no filled grid", () => {
    // An older payload (no implied_vol_filled): clean asks for filled but degrades to the clamped
    // grid rather than drawing nothing.
    render(
      <VolSurface subject="SX5E" surface={SURFACE_DENSE_THREE_ROWS} filled={true} maturities={[]} />,
    );
    const z = readZ();
    expect(z).toEqual([
      [0.3, 0.22, 0.26],
      [0.27, 0.2, 0.24],
      [0.25, 0.19, 0.23],
    ]);
  });
});
