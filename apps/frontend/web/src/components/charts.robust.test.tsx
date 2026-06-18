import { render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

vi.mock("./Plot", async () => await import("../test/plotMock"));
vi.mock("./LightweightLineChart", async () => await import("../test/lightweightLineMock"));

import type { AnalyticsMaturity } from "../api";
import { IV_SANE_MAX } from "../lib/volRobust";
import { ANALYTICS_AAA_DEGENERATE } from "../test/fixtures";
import { SmileChart, VolSurface } from "./charts";

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
