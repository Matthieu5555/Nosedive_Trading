import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { BasketLegGrid } from "./BasketLegGrid";
import type { BasketLegInput } from "../api";

function renderGrid(legs: BasketLegInput[] = []) {
  const onAdd = vi.fn();
  const onRemove = vi.fn();
  render(
    <BasketLegGrid
      legs={legs}
      defaultUnderlying="AAA"
      defaultTenor="1m"
      onAdd={onAdd}
      onRemove={onRemove}
    />,
  );
  return { onAdd, onRemove };
}

test("the grid self-labels and shows an empty-state when there are no legs", () => {
  renderGrid([]);
  expect(screen.getByRole("table", { name: /composed legs/i })).toBeInTheDocument();
  expect(screen.getByText(/No legs yet/i)).toBeInTheDocument();
});

test("adding a valid option leg calls onAdd with the composed leg", async () => {
  const user = userEvent.setup();
  const { onAdd } = renderGrid([]);
  await user.selectOptions(screen.getByLabelText("leg band"), "30dc");
  await user.click(screen.getByRole("button", { name: "Add leg" }));
  expect(onAdd).toHaveBeenCalledWith({
    instrument_kind: "option",
    side: "long",
    quantity: 1,
    underlying: "AAA",
    tenor_label: "1m",
    delta_band: "30dc",
  });
});

test("a long leg with a negative quantity is rejected user-side, onAdd not called", async () => {
  const user = userEvent.setup();
  const { onAdd } = renderGrid([]);
  const qty = screen.getByLabelText("leg quantity");
  await user.clear(qty);
  await user.type(qty, "-1");
  await user.click(screen.getByRole("button", { name: "Add leg" }));
  expect(screen.getByRole("alert")).toHaveTextContent(/long leg must have a positive quantity/i);
  expect(onAdd).not.toHaveBeenCalled();
});

test("renders an existing leg and can remove it", async () => {
  const user = userEvent.setup();
  const { onRemove } = renderGrid([
    { instrument_kind: "option", side: "long", quantity: 1, underlying: "AAA", tenor_label: "1m", delta_band: "atm" },
  ]);
  const legs = screen.getByRole("table", { name: /composed legs/i });
  expect(within(legs).getByText("atm")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /remove leg 1/i }));
  expect(onRemove).toHaveBeenCalledWith(0);
});
