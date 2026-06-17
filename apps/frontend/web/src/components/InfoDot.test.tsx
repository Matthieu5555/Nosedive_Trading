import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import { InfoDot } from "./InfoDot";

describe("InfoDot", () => {
  test("renders a quiet, labelled trigger and no tooltip until opened", () => {
    render(<InfoDot label="Nappe de volatilité" body="vol vs log-moneyness vs maturité" />);
    const trigger = screen.getByRole("button", { name: "Nappe de volatilité" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  test("hover opens the tooltip with the body and wires aria-describedby", async () => {
    const user = userEvent.setup();
    render(<InfoDot label="Nappe" body="vol vs log-moneyness vs maturité" />);
    const trigger = screen.getByRole("button", { name: "Nappe" });
    await user.hover(trigger);
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("vol vs log-moneyness vs maturité");
    expect(trigger).toHaveAttribute("aria-describedby", tip.id);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });

  test("keyboard focus opens the tooltip (keyboard-reachable)", async () => {
    const user = userEvent.setup();
    render(<InfoDot label="Smile" body="puts ◄ ATM ► calls" />);
    await user.tab();
    expect(screen.getByRole("button", { name: "Smile" })).toHaveFocus();
    expect(screen.getByRole("tooltip")).toHaveTextContent("puts ◄ ATM ► calls");
  });

  test("Escape closes an open tooltip", async () => {
    const user = userEvent.setup();
    render(<InfoDot label="Smile" body="puts ◄ ATM ► calls" />);
    await user.tab();
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  test("blur closes the tooltip", async () => {
    const user = userEvent.setup();
    render(
      <>
        <InfoDot label="Smile" body="puts ◄ ATM ► calls" />
        <button type="button">elsewhere</button>
      </>,
    );
    await user.tab();
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    await user.tab();
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  test("is non-modal: opening never inserts a dialog and the page stays reachable", async () => {
    const user = userEvent.setup();
    render(
      <>
        <InfoDot label="Smile" body="puts ◄ ATM ► calls" />
        <button type="button">behind</button>
      </>,
    );
    await user.hover(screen.getByRole("button", { name: "Smile" }));
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "behind" })).toBeEnabled();
  });

  test("an empty body renders nothing — no empty bubble (help is silent, not loud)", () => {
    const { container } = render(<InfoDot label="Unknown" body="" />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
