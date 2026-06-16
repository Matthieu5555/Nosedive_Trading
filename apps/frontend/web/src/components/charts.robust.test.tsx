import { render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

vi.mock("./Plot", async () => await import("../test/plotMock"));
vi.mock("./LightweightLineChart", async () => await import("../test/lightweightLineMock"));

import type { AnalyticsMaturity } from "../api";
import { IV_SANE_MAX } from "../lib/volRobust";
import { ANALYTICS_AAA_DEGENERATE } from "../test/fixtures";
import { GreeksTermStructure, SmileChart, VolSurface } from "./charts";

const DEGEN = ANALYTICS_AAA_DEGENERATE;
const DEGEN_MATURITY: AnalyticsMaturity = DEGEN.maturities[0];

describe("VolSurface dense nappe robustness", () => {
  test("clamps out-of-band cells to null, collapses duplicate columns, and flags the slice", () => {
    render(<VolSurface surface={DEGEN.surface} maturities={DEGEN.maturities} />);

    const fig = screen.getByLabelText(/Implied-volatility surface/i);
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
  test("drops absurd/NaN/duplicate points and notes the count, plotting only the good wings", () => {
    render(<SmileChart maturity={DEGEN_MATURITY} />);
    const fig = screen.getByLabelText(/Smile — 10d/i);

    expect(fig.getAttribute("aria-label")).toMatch(/degenerate fit/i);
    expect(fig.getAttribute("aria-label")).toMatch(/flagged/i);

    const plotted = Number(within(fig).getByTestId("line-points").textContent);
    expect(plotted).toBe(4);
  });
});

describe("GreeksTermStructure robustness", () => {
  test("excludes railed-slice points so no series carries the absurd-IV outliers", () => {
    render(<GreeksTermStructure maturities={DEGEN.maturities} currency="€" />);

    const deltaPanel = screen.getByLabelText(/Delta .* term structure/i);
    expect(within(deltaPanel).getByTestId("line-series")).toHaveTextContent("30dp");
    expect(within(deltaPanel).getByTestId("line-series")).not.toHaveTextContent("14dp");
    expect(within(deltaPanel).getByTestId("line-series")).not.toHaveTextContent("12dp");
  });
});
