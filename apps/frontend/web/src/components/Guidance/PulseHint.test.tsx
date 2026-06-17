import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import { PulseHint } from "./PulseHint";

describe("PulseHint", () => {
  test("inactive: renders the child with no pulse wrapper", () => {
    render(
      <PulseHint active={false}>
        <button type="button">Index</button>
      </PulseHint>,
    );
    expect(screen.getByRole("button", { name: "Index" })).toBeInTheDocument();
    expect(document.querySelector(".pulse-hint")).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
  });

  test("active: wraps the child in the pulse-hint emphasis", () => {
    render(
      <PulseHint active label="Commencez ici">
        <button type="button">Index</button>
      </PulseHint>,
    );
    const wrap = document.querySelector(".pulse-hint");
    expect(wrap).not.toBeNull();
    expect(wrap).toHaveAttribute("data-pulse-hint", "active");
    expect(wrap).toContainElement(screen.getByRole("button", { name: "Index" }));
    expect(screen.getByRole("note", { name: "Commencez ici" })).toBe(wrap);
  });

  test("pulsing never blocks interaction with the wrapped control", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();
    render(
      <PulseHint active>
        <button type="button" onClick={onClick}>
          Index
        </button>
      </PulseHint>,
    );
    await user.click(screen.getByRole("button", { name: "Index" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  test("the hint dies on action: re-rendering with active=false removes the pulse", () => {
    const { rerender } = render(
      <PulseHint active>
        <button type="button">Index</button>
      </PulseHint>,
    );
    expect(document.querySelector(".pulse-hint")).not.toBeNull();
    rerender(
      <PulseHint active={false}>
        <button type="button">Index</button>
      </PulseHint>,
    );
    expect(document.querySelector(".pulse-hint")).toBeNull();
  });
});
