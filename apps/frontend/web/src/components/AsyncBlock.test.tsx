import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { assertNeverBlank } from "../test/assertNeverBlank";
import { AsyncBlock } from "./AsyncBlock";
import { SKELETON_DELAY_MS } from "./Skeleton";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
});

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

describe("AsyncBlock loading branch", () => {
  test("shows no loader before the sub-second floor (P4: <1s → no loader)", () => {
    render(
      <AsyncBlock loading error={null}>
        <div>chart</div>
      </AsyncBlock>,
    );
    advance(SKELETON_DELAY_MS - 1);
    expect(screen.queryByRole("status")?.querySelector(".chart-skeleton")).toBeFalsy();
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  test("mounts a footprint-preserving skeleton once the floor elapses", () => {
    const { container } = render(
      <AsyncBlock loading error={null} height={440}>
        <div>chart</div>
      </AsyncBlock>,
    );
    advance(SKELETON_DELAY_MS);
    const block = container.querySelector(".chart-skeleton") as HTMLElement;
    expect(block).not.toBeNull();
    expect(block.getAttribute("role")).toBe("status");
    expect(block.style.height).toBe("440px");
    expect(block).toHaveTextContent("Loading…");
  });

  test("names the subject on the skeleton when given one", () => {
    render(
      <AsyncBlock loading error={null} subject="the SX5E surface">
        <div>chart</div>
      </AsyncBlock>,
    );
    advance(SKELETON_DELAY_MS);
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "Loading the SX5E surface…");
  });

  test("is never blank in either loading phase", () => {
    const early = render(
      <AsyncBlock loading error={null}>
        <div>chart</div>
      </AsyncBlock>,
    );
    assertNeverBlank(early);
    advance(SKELETON_DELAY_MS);
    assertNeverBlank(early);
  });
});

describe("AsyncBlock error and settled branches read differently", () => {
  test("error renders a loud role=alert carrying the message", () => {
    render(
      <AsyncBlock loading={false} error="surface fetch failed">
        <div>chart</div>
      </AsyncBlock>,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("surface fetch failed");
    expect(screen.queryByRole("status")).toBeNull();
  });

  test("settled renders the children and neither status nor alert", () => {
    render(
      <AsyncBlock loading={false} error={null}>
        <div>chart body</div>
      </AsyncBlock>,
    );
    expect(screen.getByText("chart body")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("loading-skeleton, error, and settled are three distinct roles", () => {
    const view = render(
      <AsyncBlock loading error={null}>
        <div>chart body</div>
      </AsyncBlock>,
    );
    advance(SKELETON_DELAY_MS);
    expect(screen.getByRole("status")).toBeInTheDocument();

    view.rerender(
      <AsyncBlock loading={false} error="boom">
        <div>chart body</div>
      </AsyncBlock>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();

    view.rerender(
      <AsyncBlock loading={false} error={null}>
        <div>chart body</div>
      </AsyncBlock>,
    );
    expect(screen.getByText("chart body")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
