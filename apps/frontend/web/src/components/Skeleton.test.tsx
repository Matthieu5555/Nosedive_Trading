import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { assertNeverBlank } from "../test/assertNeverBlank";
import { ChartSkeleton, Skeleton, SKELETON_DELAY_MS } from "./Skeleton";

describe("Skeleton", () => {
  test("is a role=status surface named 'Chargement…' by default", () => {
    render(<Skeleton />);
    const block = screen.getByRole("status");
    expect(block).toHaveAttribute("aria-label", "Chargement…");
    expect(block).toHaveTextContent("Chargement…");
  });

  test("reserves the requested footprint height", () => {
    const { container } = render(<Skeleton height={440} />);
    const block = container.querySelector(".chart-skeleton") as HTMLElement;
    expect(block.style.height).toBe("440px");
  });

  test("never renders the English 'Loading…' bare text", () => {
    render(<Skeleton />);
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  test("is never blank", () => {
    assertNeverBlank(render(<Skeleton />));
  });
});

describe("ChartSkeleton", () => {
  test("names its subject when given one", () => {
    render(<ChartSkeleton subject="la nappe SX5E" />);
    expect(screen.getByRole("status")).toHaveAttribute(
      "aria-label",
      "Chargement de la nappe SX5E…",
    );
  });

  test("falls back to the plain label without a subject", () => {
    render(<ChartSkeleton />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "Chargement…");
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
