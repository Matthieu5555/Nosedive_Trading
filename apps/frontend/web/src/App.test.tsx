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

test("the top nav is exactly the six English tabs, Operations active on load", async () => {
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Operations", level: 1 })).toBeInTheDocument();

  const links = within(mainNav())
    .getAllByRole("link")
    .map((link) => link.textContent);
  expect(links).toEqual(["Operations", "Market", "Positions", "Simulate", "Strategy", "Signals"]);
  expect(within(mainNav()).getByRole("link", { name: "Operations" })).toHaveAttribute(
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

// label → { path, heading } for each tab that navigates away from the Operations landing route.
const TABS = [
  { label: "Market", path: "/market", heading: "Market" },
  { label: "Positions", path: "/positions", heading: "Positions" },
  { label: "Simulate", path: "/simulate", heading: "Simulate" },
  { label: "Strategy", path: "/strategy", heading: "Strategy" },
  { label: "Signals", path: "/signals", heading: "Signals" },
] as const;

for (const tab of TABS) {
  test(`${tab.label} routes to ${tab.path} and shows its heading`, async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "Operations", level: 1 });

    await user.click(within(mainNav()).getByRole("link", { name: tab.label }));
    expect(await screen.findByRole("heading", { name: tab.heading, level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe(tab.path));
  });
}

const REDIRECTS = [
  // Basket and Risk Scenarios folded into Simulate; their old paths forward there.
  { from: "/basket", to: "/simulate", heading: "Simulate" },
  { from: "/risk", to: "/simulate", heading: "Simulate" },
  { from: "/risque", to: "/simulate", heading: "Simulate" },
  { from: "/ordres", to: "/strategy", heading: "Strategy" },
  { from: "/orders", to: "/strategy", heading: "Strategy" },
  // Operations now owns the index route, so its old path forwards home.
  { from: "/operations", to: "/", heading: "Operations" },
  { from: "/does-not-exist", to: "/", heading: "Operations" },
] as const;

for (const r of REDIRECTS) {
  test(`legacy ${r.from} redirects to ${r.to}`, async () => {
    window.history.pushState({}, "", r.from);
    render(<App />);

    expect(await screen.findByRole("heading", { name: r.heading, level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe(r.to));
  });
}

test("the floating assistant launcher rides along on a non-home route", async () => {
  window.history.pushState({}, "", "/market");
  render(<App />);

  // It is mounted globally outside <Routes>, so it shows up on Market just as on Operations.
  expect(await screen.findByRole("heading", { name: "Market", level: 1 })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ask the assistant" })).toBeInTheDocument();
});
