import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { Metric } from "./Metric";

describe("Metric — the number law (never a naked number)", () => {
  test("a numeric value + unit routes through the sci/unit idiom", () => {
    // Oracle (lib/format.sciUnit): 0.58 → "5.8 × 10⁻¹" with its unit appended. Derived by hand from
    // the documented rule (analytics-display memory), not copied from the component.
    render(<Metric label="Delta" value={0.58} unit="$/$" />);
    expect(screen.getByText("5.8 × 10⁻¹ $/$")).toBeInTheDocument();
  });

  test("a numeric value with no unit still renders sci-notation (never a fixed-decimal naked float)", () => {
    render(<Metric label="Sharpe" value={1.5} />);
    // 1.5 → "1.5 × 10⁰"; the point is it is NOT the bare string "1.5".
    expect(screen.getByText("1.5 × 10⁰")).toBeInTheDocument();
    expect(screen.queryByText("1.5")).not.toBeInTheDocument();
  });

  test("a null/undefined numeric value reads the honest em-dash, never blank", () => {
    render(<Metric label="Net P&L" value={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  test("a pre-formatted string value (existing callers) renders verbatim", () => {
    // BacktestResults etc. already pre-format via sciUnit and pass a string; do not regress them.
    render(<Metric label="Net P&L" value="5.8 × 10⁻¹ $" />);
    expect(screen.getByText("5.8 × 10⁻¹ $")).toBeInTheDocument();
  });

  test("a string value + unit appends the unit (a date/enum string stays naked)", () => {
    render(<Metric label="Vol shock" value="0.05" unit="(frac)" />);
    expect(screen.getByText("0.05 (frac)")).toBeInTheDocument();
  });

  test("a hint mounts a provenance InfoDot; absent hint mounts none", () => {
    const { rerender } = render(
      <Metric label="Risk computed for" value="2026-06-17" hint="from the offline store" />,
    );
    expect(screen.getByRole("button", { name: /provenance/i })).toBeInTheDocument();
    rerender(<Metric label="Risk computed for" value="2026-06-17" />);
    expect(screen.queryByRole("button", { name: /provenance/i })).not.toBeInTheDocument();
  });
});
