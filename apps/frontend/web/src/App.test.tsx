import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("./components/Plot", async () => await import("./test/plotMock"));
vi.mock("./components/CandleChart", async () => await import("./test/candleMock"));
vi.mock(
  "./components/LightweightLineChart",
  async () => await import("./test/lightweightLineMock"),
);

import { App } from "./App";
import { RECORDED_EMPTY } from "./test/fixtures";
import { renderWithClient as render } from "./test/renderWithClient";
import { jsonGet, server } from "./test/server";

beforeEach(() => {
  server.use(jsonGet("/api/recorded-dates", RECORDED_EMPTY));
});

afterEach(() => {
  window.history.pushState({}, "", "/");
});

function mainNav() {
  return screen.getByRole("navigation", { name: "Main" });
}

test("the top nav is exactly the three onglets — Données, Risque, Ordres — Données active on load", async () => {
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Données", level: 1 })).toBeInTheDocument();

  const links = within(mainNav())
    .getAllByRole("link")
    .map((link) => link.textContent);
  expect(links).toEqual(["Données", "Risque", "Ordres"]);
  expect(within(mainNav()).getByRole("link", { name: "Données" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("the dropped 7-tab labels are gone from the main nav", () => {
  render(<App />);
  for (const gone of ["Market", "Basket", "Risk Scenarios", "Signals", "Strategy", "Positions"]) {
    expect(within(mainNav()).queryByRole("link", { name: gone })).not.toBeInTheDocument();
  }
});

test("Risque routes to /risque and shows its heading", async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "Données", level: 1 });

  await user.click(within(mainNav()).getByRole("link", { name: "Risque" }));
  expect(await screen.findByRole("heading", { name: "Risque", level: 1 })).toBeInTheDocument();
  await waitFor(() => expect(window.location.pathname).toBe("/risque"));
});

test("Ordres routes to /ordres and shows its heading", async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: "Données", level: 1 });

  await user.click(within(mainNav()).getByRole("link", { name: "Ordres" }));
  expect(await screen.findByRole("heading", { name: "Ordres", level: 1 })).toBeInTheDocument();
  await waitFor(() => expect(window.location.pathname).toBe("/ordres"));
});

test("Operations is a secondary utility — addressable, but NOT a top-level onglet", async () => {
  render(<App />);
  // Not in the main nav…
  expect(within(mainNav()).queryByRole("link", { name: "Operations" })).not.toBeInTheDocument();
  // …but still a reachable utility link.
  expect(screen.getByRole("link", { name: "Operations" })).toBeInTheDocument();

  window.history.pushState({}, "", "/operations");
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Operations", level: 1 })).toBeInTheDocument();
});

const REDIRECTS = [
  { from: "/market", to: "/", heading: "Données" },
  { from: "/basket", to: "/risque", heading: "Risque" },
  { from: "/risk", to: "/risque", heading: "Risque" },
  { from: "/positions", to: "/risque", heading: "Risque" },
  { from: "/orders", to: "/ordres", heading: "Ordres" },
  { from: "/strategy", to: "/ordres", heading: "Ordres" },
  { from: "/signals", to: "/", heading: "Données" },
  { from: "/does-not-exist", to: "/", heading: "Données" },
] as const;

for (const r of REDIRECTS) {
  test(`legacy ${r.from} redirects to ${r.to}`, async () => {
    window.history.pushState({}, "", r.from);
    render(<App />);

    expect(await screen.findByRole("heading", { name: r.heading, level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe(r.to));
  });
}

test("there is no Orders nav button in the main nav", () => {
  render(<App />);
  expect(within(mainNav()).queryByRole("link", { name: "Orders" })).not.toBeInTheDocument();
});
