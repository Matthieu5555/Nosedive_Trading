import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { BasketLegGrid } from "./BasketLegGrid";
import type { BasketLegInput } from "../api";

// A 32-band axis the page would thread from GET /api/config/delta-bands; derived here by hand
// (put→ATM→call) independently of any backend output, with bands the hard-coded 8-list lacks
// (e.g. 02dp / 02dc) so a test can prove the selector is driven by the prop, not a const.
const BANDS_32 = [
  "30dp", "28dp", "26dp", "24dp", "22dp", "20dp", "18dp", "16dp",
  "14dp", "12dp", "10dp", "08dp", "06dp", "04dp", "02dp",
  "atm", "atmp",
  "02dc", "04dc", "06dc", "08dc", "10dc", "12dc", "14dc", "16dc",
  "18dc", "20dc", "22dc", "24dc", "26dc", "28dc", "30dc",
];

function renderGrid(legs: BasketLegInput[] = [], bands?: string[]) {
  const onAdd = vi.fn();
  const onRemove = vi.fn();
  render(
    <BasketLegGrid
      legs={legs}
      defaultUnderlying="AAA"
      defaultTenor="1m"
      bands={bands}
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

test("the band selector is driven by the bands prop, not a hard-coded list", () => {
  renderGrid([], BANDS_32);
  const bandSelect = screen.getByLabelText("leg band");
  const options = within(bandSelect).getAllByRole("option");
  // All 32 platform bands are offered (the old hard-coded list was only 8).
  expect(options).toHaveLength(32);
  // Bands present only in the 32-band axis (the hard-coded 8-list never had these).
  expect(within(bandSelect).getByRole("option", { name: "30dp" })).toBeInTheDocument();
  expect(within(bandSelect).getByRole("option", { name: "02dp" })).toBeInTheDocument();
  expect(within(bandSelect).getByRole("option", { name: "02dc" })).toBeInTheDocument();
  expect(within(bandSelect).getByRole("option", { name: "30dc" })).toBeInTheDocument();
});

test("with no bands (loading/error) the selector still renders a usable fallback", () => {
  renderGrid([]);
  const bandSelect = screen.getByLabelText("leg band");
  const options = within(bandSelect).getAllByRole("option");
  // The minimal fallback keeps the form usable rather than rendering an empty selector.
  expect(options.length).toBeGreaterThan(0);
  expect(within(bandSelect).getByRole("option", { name: "atm" })).toBeInTheDocument();
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
