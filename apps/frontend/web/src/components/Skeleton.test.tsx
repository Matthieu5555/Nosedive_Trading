import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { assertNeverBlank } from "../test/assertNeverBlank";
import { ChartSkeleton, Skeleton, SKELETON_DELAY_MS } from "./Skeleton";

describe("Skeleton", () => {
  test("is a role=status surface named 'Loading…' by default", () => {
    render(<Skeleton />);
    const block = screen.getByRole("status");
    expect(block).toHaveAttribute("aria-label", "Loading…");
    expect(block).toHaveTextContent("Loading…");
  });

  test("reserves the requested footprint height", () => {
    const { container } = render(<Skeleton height={440} />);
    const block = container.querySelector(".chart-skeleton") as HTMLElement;
    expect(block.style.height).toBe("440px");
  });

  test("is never blank", () => {
    assertNeverBlank(render(<Skeleton />));
  });
});

describe("ChartSkeleton", () => {
  test("names its subject when given one", () => {
    render(<ChartSkeleton subject="the SX5E surface" />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "Loading the SX5E surface…");
  });

  test("falls back to the plain label without a subject", () => {
    render(<ChartSkeleton />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "Loading…");
  });

  test("defaults to the 440px chart footprint", () => {
    const { container } = render(<ChartSkeleton />);
    const block = container.querySelector(".chart-skeleton") as HTMLElement;
    expect(block.style.height).toBe("440px");
  });
});

describe("SKELETON_DELAY_MS", () => {
  test("is the 1s sub-second floor from the design-language P4 table", () => {
    expect(SKELETON_DELAY_MS).toBe(1000);
  });
});
