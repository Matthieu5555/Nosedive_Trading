import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import type { BasketLegInput } from "../api";
import { BookProvider, useBook } from "./bookContext";

const LEG: BasketLegInput = {
  instrument_kind: "option",
  side: "long",
  quantity: 1,
  underlying: "SX5E",
  tenor_label: "1m",
  delta_band: "atm",
};

function Harness() {
  const book = useBook();
  return (
    <div>
      <span data-testid="underlying">{book.underlying || "—"}</span>
      <span data-testid="legs">{book.legs.length}</span>
      <button type="button" onClick={() => book.setUnderlying("SX5E")}>
        set underlying
      </button>
      <button type="button" onClick={() => book.addLeg(LEG)}>
        add leg
      </button>
      <button type="button" onClick={() => book.removeLeg(0)}>
        remove leg
      </button>
      <button type="button" onClick={() => book.clearLegs()}>
        clear
      </button>
    </div>
  );
}

test("without a provider the book is an inert default (no throw, no-op setters)", async () => {
  const user = userEvent.setup();
  render(<Harness />);

  expect(screen.getByTestId("underlying")).toHaveTextContent("—");
  expect(screen.getByTestId("legs")).toHaveTextContent("0");

  // The default setters are no-ops: clicking changes nothing, but never throws.
  await user.click(screen.getByRole("button", { name: "set underlying" }));
  await user.click(screen.getByRole("button", { name: "add leg" }));
  expect(screen.getByTestId("underlying")).toHaveTextContent("—");
  expect(screen.getByTestId("legs")).toHaveTextContent("0");
});

test("the provider carries underlying + legs across consumers (add / remove / clear)", async () => {
  const user = userEvent.setup();
  render(
    <BookProvider>
      <Harness />
    </BookProvider>,
  );

  await user.click(screen.getByRole("button", { name: "set underlying" }));
  expect(screen.getByTestId("underlying")).toHaveTextContent("SX5E");

  await user.click(screen.getByRole("button", { name: "add leg" }));
  await user.click(screen.getByRole("button", { name: "add leg" }));
  expect(screen.getByTestId("legs")).toHaveTextContent("2");

  await user.click(screen.getByRole("button", { name: "remove leg" }));
  expect(screen.getByTestId("legs")).toHaveTextContent("1");

  await user.click(screen.getByRole("button", { name: "clear" }));
  expect(screen.getByTestId("legs")).toHaveTextContent("0");
});
