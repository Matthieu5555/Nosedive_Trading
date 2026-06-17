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
