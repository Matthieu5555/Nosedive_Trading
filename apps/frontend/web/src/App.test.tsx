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

test("the top nav is exactly the seven English tabs — Market active on load", async () => {
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Market", level: 1 })).toBeInTheDocument();

  const links = within(mainNav())
    .getAllByRole("link")
    .map((link) => link.textContent);
  expect(links).toEqual([
    "Market",
    "Basket",
    "Signals",
    "Strategy",
    "Risk Scenarios",
    "Positions",
    "Operations",
  ]);
  expect(within(mainNav()).getByRole("link", { name: "Market" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});

test("the French 3-tab labels are gone from the main nav", () => {
  render(<App />);
  for (const gone of ["Données", "Risque", "Ordres"]) {
    expect(within(mainNav()).queryByRole("link", { name: gone })).not.toBeInTheDocument();
  }
});

// label → { path, heading } for each tab that navigates to its own route.
const TABS = [
  { label: "Basket", path: "/basket", heading: "Basket Builder" },
  { label: "Signals", path: "/signals", heading: "Signals" },
  { label: "Strategy", path: "/strategy", heading: "Strategy" },
  { label: "Risk Scenarios", path: "/risk", heading: "Risk Scenarios" },
  { label: "Positions", path: "/positions", heading: "Positions" },
  { label: "Operations", path: "/operations", heading: "Operations" },
] as const;

for (const tab of TABS) {
  test(`${tab.label} routes to ${tab.path} and shows its heading`, async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "Market", level: 1 });

    await user.click(within(mainNav()).getByRole("link", { name: tab.label }));
    expect(await screen.findByRole("heading", { name: tab.heading, level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe(tab.path));
  });
}

const REDIRECTS = [
  // The retired French 3-tab paths forward to their 7-tab homes.
  { from: "/risque", to: "/basket", heading: "Basket Builder" },
  { from: "/ordres", to: "/strategy", heading: "Strategy" },
  { from: "/orders", to: "/strategy", heading: "Strategy" },
  { from: "/market", to: "/", heading: "Market" },
  { from: "/does-not-exist", to: "/", heading: "Market" },
] as const;

for (const r of REDIRECTS) {
  test(`legacy ${r.from} redirects to ${r.to}`, async () => {
    window.history.pushState({}, "", r.from);
    render(<App />);

    expect(await screen.findByRole("heading", { name: r.heading, level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe(r.to));
  });
}
