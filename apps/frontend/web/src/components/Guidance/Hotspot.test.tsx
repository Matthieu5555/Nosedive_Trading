import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test } from "vitest";

import { Hotspot } from "./Hotspot";

describe("Hotspot", () => {
  test("renders a quiet ⓘ trigger labelled by the metric, tooltip closed until asked", () => {
    render(<Hotspot label="Nappe de volatilité" body="vol vs log-moneyness vs maturité" />);
    expect(
      screen.getByRole("button", { name: "Nappe de volatilité" }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  test("hover reveals the gloss just-in-time (the tier-2 carrier)", async () => {
    const user = userEvent.setup();
    render(<Hotspot label="Dispersion (ρ̄)" body="corrélation implicite moyenne du panier" />);
    await user.hover(screen.getByRole("button", { name: "Dispersion (ρ̄)" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent(
      "corrélation implicite moyenne du panier",
    );
  });

  test("opening the hotspot never inserts a modal/dialog — the page stays interactive", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Hotspot label="Prix" body="OHLC quotidien de l'indice" />
        <button type="button">behind</button>
      </>,
    );
    await user.hover(screen.getByRole("button", { name: "Prix" }));
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "behind" })).toBeEnabled();
  });

  test("an empty gloss renders nothing — no empty hotspot bubble", () => {
    render(<Hotspot label="Inconnu" body="" />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.querySelector(".info-dot")).toBeNull();
  });
});
