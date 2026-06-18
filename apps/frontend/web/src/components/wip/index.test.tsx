// ----------------------------------------------------------------------------------------------
// WIP module contract test. The WIP module owns the "not ready yet" concern at two granularities:
// the <WIP> element wrapper (dim + inert + corner badge) and the FEATURE_STATUS registry that flags
// whole tabs. This pins the emitted contract — classes, aria-disabled, the inert veil, the registry
// default and the placeholder copy — so a silent regression in the disabled look or the opt-in
// semantics is caught. Expected values are re-derived by hand here, not imported from the module.
// ----------------------------------------------------------------------------------------------

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { FEATURE_STATUS, featureStatus, isWip, WIP, WipPlaceholder, WipTag } from "./index";

afterEach(() => {
  // FEATURE_STATUS is a shared mutable registry; tests that flag a path must not leak into the next.
  for (const key of Object.keys(FEATURE_STATUS)) delete FEATURE_STATUS[key];
});

describe("FEATURE_STATUS registry", () => {
  test("an unknown path is ready (opt-in only) and not wip", () => {
    expect(featureStatus("/anything").status).toBe("ready");
    expect(isWip("/anything")).toBe(false);
  });

  test("nothing is flagged by default", () => {
    expect(Object.keys(FEATURE_STATUS)).toHaveLength(0);
  });

  test("flagging a path makes it wip and carries the reason", () => {
    FEATURE_STATUS["/signals"] = { status: "wip", reason: "engine not wired" };
    expect(isWip("/signals")).toBe(true);
    expect(featureStatus("/signals").reason).toBe("engine not wired");
  });
});

describe("WipTag", () => {
  test("renders the WIP label with a default tooltip + aria-label", () => {
    render(<WipTag />);
    const tag = screen.getByText("WIP");
    expect(tag.classList.contains("wip__tag")).toBe(true);
    expect(tag.getAttribute("title")).toBe("Work in progress");
    expect(tag.getAttribute("aria-label")).toBe("Work in progress");
  });

  test("a reason flows into both the title and the aria-label", () => {
    render(<WipTag reason="backtest TBD" />);
    const tag = screen.getByText("WIP");
    expect(tag.getAttribute("title")).toBe("backtest TBD");
    expect(tag.getAttribute("aria-label")).toBe("Work in progress: backtest TBD");
  });
});

describe("WIP wrapper", () => {
  test("marks the wrapper disabled and veils the children inert", () => {
    const { container } = render(
      <WIP reason="soon">
        <button type="button">Run</button>
      </WIP>,
    );
    const wrap = container.firstElementChild as HTMLElement;
    expect(wrap.classList.contains("wip")).toBe(true);
    expect(wrap.getAttribute("aria-disabled")).toBe("true");
    expect(wrap.getAttribute("data-wip")).toBe("true");

    const veil = wrap.querySelector(".wip__content") as HTMLElement;
    // `inert` is what actually makes the half-built children untouchable; pin it.
    expect(veil.hasAttribute("inert")).toBe(true);
    // The real child still renders inside the veil (the user sees the shape of what's coming).
    expect(veil.querySelector("button")?.textContent).toBe("Run");
  });

  test("the corner badge carries the reason", () => {
    render(
      <WIP reason="soon">
        <span>x</span>
      </WIP>,
    );
    expect(screen.getByText("WIP").getAttribute("title")).toBe("soon");
  });
});

describe("WipPlaceholder", () => {
  test("with a title, says the page is a work in progress and shows the reason", () => {
    render(<WipPlaceholder title="Strategy" reason="backtest not wired" />);
    expect(screen.getByText("Strategy is a work in progress")).toBeTruthy();
    expect(screen.getByText("backtest not wired")).toBeTruthy();
    expect(screen.getByRole("status")).toBeTruthy();
  });

  test("without a title, falls back to a bare label and omits the reason line", () => {
    const { container } = render(<WipPlaceholder />);
    expect(screen.getByText("Work in progress")).toBeTruthy();
    expect(container.querySelector(".wip-placeholder__reason")).toBeNull();
  });
});
