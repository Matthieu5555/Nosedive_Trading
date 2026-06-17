import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, test, vi } from "vitest";

vi.mock("../../components/Plot", async () => await import("../../test/plotMock"));

import type { ComposeLayerInput } from "../../api";
import { ComposeTab } from "./ComposeTab";

// A small host that wires the layer list state the way Basket.tsx does, so the test exercises the
// real add/remove/reorder callbacks against rendered rows (user-facing).
function Host() {
  const [layers, setLayers] = useState<ComposeLayerInput[]>([]);
  return (
    <ComposeTab
      subStrategies={["SX5E", "SPX"]}
      subStrategiesLoading={false}
      subStrategiesError={null}
      layers={layers}
      bands={["atm", "30dc", "30dp"]}
      loading={false}
      error={null}
      book={null}
      currency="$"
      tradeDate=""
      onAddLayer={(layer) => setLayers((c) => [...c, layer])}
      onRemoveLayer={(i) => setLayers((c) => c.filter((_, idx) => idx !== i))}
      onMoveLayer={(i, dir) =>
        setLayers((c) => {
          const t = i + dir;
          if (t < 0 || t >= c.length) return c;
          const next = [...c];
          [next[i], next[t]] = [next[t], next[i]];
          return next;
        })
      }
      onCompose={() => {}}
    />
  );
}

function rowLabels(): string[] {
  const table = screen.getByRole("table", { name: /composed layers/i });
  return within(table)
    .getAllByRole("row")
    .slice(1) // drop the header row
    .map((row) => within(row).getAllByRole("cell")[1]?.textContent ?? ""); // [1] = the Label column
}

test("an empty composition renders a labelled empty state, not a blank table", () => {
  render(<Host />);
  expect(screen.getByText(/No layers yet/i)).toBeInTheDocument();
});

test("adding labelled layers renders them as ordered rows in the layer list", async () => {
  const user = userEvent.setup();
  render(<Host />);

  await user.type(screen.getByLabelText("layer label"), "S1 dispersion");
  await user.click(screen.getByRole("button", { name: "add layer" }));

  await user.type(screen.getByLabelText("layer label"), "S2 put line");
  await user.click(screen.getByRole("button", { name: "add layer" }));

  expect(rowLabels()).toEqual(["S1 dispersion", "S2 put line"]);
});

test("reordering moves a layer up — display order changes, the selection is honoured", async () => {
  const user = userEvent.setup();
  render(<Host />);

  await user.type(screen.getByLabelText("layer label"), "S1 dispersion");
  await user.click(screen.getByRole("button", { name: "add layer" }));
  await user.type(screen.getByLabelText("layer label"), "S2 put line");
  await user.click(screen.getByRole("button", { name: "add layer" }));

  expect(rowLabels()).toEqual(["S1 dispersion", "S2 put line"]);
  await user.click(screen.getByRole("button", { name: /move layer 2 up/i }));
  expect(rowLabels()).toEqual(["S2 put line", "S1 dispersion"]);
});

test("removing a layer drops exactly that row", async () => {
  const user = userEvent.setup();
  render(<Host />);

  await user.type(screen.getByLabelText("layer label"), "S1 dispersion");
  await user.click(screen.getByRole("button", { name: "add layer" }));
  await user.type(screen.getByLabelText("layer label"), "S2 put line");
  await user.click(screen.getByRole("button", { name: "add layer" }));

  await user.click(screen.getByRole("button", { name: /remove layer 1/i }));
  expect(rowLabels()).toEqual(["S2 put line"]);
});
