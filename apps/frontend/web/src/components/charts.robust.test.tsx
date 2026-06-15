// Render-layer robustness for the IV surface, smile, and Greek term structure against a
// degenerate slice (absurd railed IV + NaN + duplicate log-moneyness). The canvas wrappers are
// stubbed (jsdom has no canvas): the Plot stub exposes the surface z-grid and the LightweightLine
// stub exposes the plotted point count, so a test can assert WHAT was handed to the chart — the
// clamped/excluded geometry, never the blown-up raw data.

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
    // The flag note rides the panel label: 1 slice flagged.
    const fig = screen.getByLabelText(/Implied-volatility surface/i);
    expect(fig.getAttribute("aria-label")).toMatch(/1 slice flagged/i);
    // The z-grid the surface received: the 1.4 railed cell became null; every finite cell is
    // inside the sane band, so the height/colour scale cannot be blown. Duplicate -0.1 collapsed.
    const z = JSON.parse(within(fig).getByTestId("plot-z").textContent || "[]") as (
      | number
      | null
    )[][];
    expect(z[0].length).toBe(4); // 5 columns with a dup -0.1 → 4
    const finite = z.flat().filter((v): v is number => typeof v === "number");
    expect(Math.max(...finite)).toBeLessThanOrEqual(IV_SANE_MAX);
    expect(z[0]).toContain(null); // the railed 1.4 cell is a hole, not a 1.4 spike
  });
});

describe("SmileChart robustness", () => {
  test("drops absurd/NaN/duplicate points and notes the count, plotting only the good wings", () => {
    render(<SmileChart maturity={DEGEN_MATURITY} />);
    const fig = screen.getByLabelText(/Smile — 10d/i);
    // The degenerate fit is flagged, and the dropped-point count is noted.
    expect(fig.getAttribute("aria-label")).toMatch(/degenerate fit/i);
    expect(fig.getAttribute("aria-label")).toMatch(/flagged/i);
    // 6 raw points → 3 good (one absurd, one NaN, one duplicate-k dropped). The smile shares the
    // ATM (k=0) point across both wings, so the plotted total is 3 + 1 (ATM counted in both) = 4.
    const plotted = Number(within(fig).getByTestId("line-points").textContent);
    expect(plotted).toBe(4);
  });
});

describe("GreeksTermStructure robustness", () => {
  test("excludes railed-slice points so no series carries the absurd-IV outliers", () => {
    render(<GreeksTermStructure maturities={DEGEN.maturities} currency="€" />);
    // Only the good 30dp point survives (14dp/12dp sit on the railed slice, IV out of band), so
    // each Greek panel plots exactly one point — never the railed outliers that spiked the panel.
    const deltaPanel = screen.getByLabelText(/Delta .* term structure/i);
    expect(within(deltaPanel).getByTestId("line-series")).toHaveTextContent("30dp");
    expect(within(deltaPanel).getByTestId("line-series")).not.toHaveTextContent("14dp");
    expect(within(deltaPanel).getByTestId("line-series")).not.toHaveTextContent("12dp");
  });
});
