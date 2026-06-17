// ----------------------------------------------------------------------------------------------
// LAYOUT PRIMITIVES contract test. These four primitives (Stack/Cluster/Grid/Scroll) are the one
// owner of spacing and overflow: pages say WHAT they want (a t-shirt size) and the primitive emits
// the class + the --l-* CSS variables that foundation.css consumes. This test pins that emitted
// contract (class name + which --space-* token each size maps to + the Scroll a11y wiring), so a
// silent change to the size map or the role/tabIndex logic is caught. It tests CURRENT behavior of
// layout/index.tsx as-is; it does not change the component.
// ----------------------------------------------------------------------------------------------

import { render } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { Cluster, Grid, Scroll, Stack } from "./index";

// The size -> token mapping is the contract under test, re-derived here by hand from the documented
// rule ("the only legal spacing steps map to the --space-* tokens"), NOT imported from the module.
const EXPECTED_SPACE_VAR: Record<string, string> = {
  none: "var(--space-0)",
  "3xs": "var(--space-3xs)",
  "2xs": "var(--space-2xs)",
  xs: "var(--space-xs)",
  sm: "var(--space-sm)",
  md: "var(--space-md)",
  lg: "var(--space-lg)",
  xl: "var(--space-xl)",
  "2xl": "var(--space-2xl)",
  "3xl": "var(--space-3xl)",
};

describe("Stack", () => {
  test("renders .l-stack and sets --l-gap from the default size (md)", () => {
    const { container } = render(<Stack>x</Stack>);
    const el = container.firstElementChild as HTMLElement;
    expect(el.classList.contains("l-stack")).toBe(true);
    expect(el.style.getPropertyValue("--l-gap")).toBe(EXPECTED_SPACE_VAR.md);
  });

  test("each size maps to its --space-* token", () => {
    for (const [size, token] of Object.entries(EXPECTED_SPACE_VAR)) {
      const { container } = render(<Stack gap={size as never}>x</Stack>);
      const el = container.firstElementChild as HTMLElement;
      expect(el.style.getPropertyValue("--l-gap"), `Stack gap=${size}`).toBe(token);
    }
  });

  test("align passes through to alignItems; extra className is merged", () => {
    const { container } = render(
      <Stack align="center" className="extra">
        x
      </Stack>,
    );
    const el = container.firstElementChild as HTMLElement;
    expect(el.style.alignItems).toBe("center");
    expect(el.classList.contains("l-stack")).toBe(true);
    expect(el.classList.contains("extra")).toBe(true);
  });
});

describe("Cluster", () => {
  test("renders .l-cluster with the default gap (sm) and align/justify vars", () => {
    const { container } = render(<Cluster>x</Cluster>);
    const el = container.firstElementChild as HTMLElement;
    expect(el.classList.contains("l-cluster")).toBe(true);
    expect(el.style.getPropertyValue("--l-gap")).toBe(EXPECTED_SPACE_VAR.sm);
    // Defaults: align center, justify flex-start.
    expect(el.style.getPropertyValue("--l-align")).toBe("center");
    expect(el.style.getPropertyValue("--l-justify")).toBe("flex-start");
  });

  test("gap + justify overrides flow into the --l-* vars", () => {
    const { container } = render(
      <Cluster gap="lg" justify="space-between">
        x
      </Cluster>,
    );
    const el = container.firstElementChild as HTMLElement;
    expect(el.style.getPropertyValue("--l-gap")).toBe(EXPECTED_SPACE_VAR.lg);
    expect(el.style.getPropertyValue("--l-justify")).toBe("space-between");
  });
});

describe("Grid", () => {
  test("renders .l-grid with the default gap (md) and default min track (240px)", () => {
    const { container } = render(<Grid>x</Grid>);
    const el = container.firstElementChild as HTMLElement;
    expect(el.classList.contains("l-grid")).toBe(true);
    expect(el.style.getPropertyValue("--l-gap")).toBe(EXPECTED_SPACE_VAR.md);
    expect(el.style.getPropertyValue("--l-min")).toBe("240px");
  });

  test("a custom min column width flows into --l-min", () => {
    const { container } = render(<Grid min="320px">x</Grid>);
    const el = container.firstElementChild as HTMLElement;
    expect(el.style.getPropertyValue("--l-min")).toBe("320px");
  });
});

describe("Scroll", () => {
  test("with NO label: plain container, no region role / tabIndex", () => {
    const { container } = render(<Scroll>x</Scroll>);
    const el = container.firstElementChild as HTMLElement;
    expect(el.classList.contains("l-scroll")).toBe(true);
    expect(el.getAttribute("role")).toBeNull();
    expect(el.getAttribute("aria-label")).toBeNull();
    expect(el.getAttribute("tabindex")).toBeNull();
  });

  test("WITH a label: becomes a focusable, named scroll region", () => {
    const { container } = render(<Scroll label="Greeks table">x</Scroll>);
    const el = container.firstElementChild as HTMLElement;
    expect(el.getAttribute("role")).toBe("region");
    expect(el.getAttribute("aria-label")).toBe("Greeks table");
    expect(el.getAttribute("tabindex")).toBe("0");
  });
});
