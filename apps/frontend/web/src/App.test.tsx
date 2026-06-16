import { screen, waitFor } from "@testing-library/react";
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

test("top navigation reaches Market, Basket, and Risk Scenarios", async () => {
  const user = userEvent.setup();
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Market" })).toBeInTheDocument();

  await user.click(screen.getByRole("link", { name: "Basket" }));
  expect(await screen.findByRole("heading", { name: "Basket Builder" })).toBeInTheDocument();
  await waitFor(() => expect(window.location.pathname).toBe("/basket"));

  await user.click(screen.getByRole("link", { name: "Risk Scenarios" }));
  expect(await screen.findByRole("heading", { name: "Risk Scenarios" })).toBeInTheDocument();
  await waitFor(() => expect(window.location.pathname).toBe("/risk"));
});

const STUB_TABS = [
  { link: "Operations", heading: "Operations", path: "/operations" },
  { link: "Signals", heading: "Signals", path: "/signals" },
  { link: "Strategy", heading: "Strategy", path: "/strategy" },
  { link: "Positions", heading: "Positions", path: "/positions" },
] as const;

test("top navigation reaches the four scaffold tabs, each on an empty-state stub", async () => {
  const user = userEvent.setup();
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Market" })).toBeInTheDocument();

  for (const tab of STUB_TABS) {
    await user.click(screen.getByRole("link", { name: tab.link }));
    expect(await screen.findByRole("heading", { name: tab.heading, level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe(tab.path));
    expect(screen.getByText("No data yet")).toBeInTheDocument();
  }
});

for (const tab of STUB_TABS) {
  test(`${tab.heading} is directly addressable and marks its nav link active`, async () => {
    window.history.pushState({}, "", tab.path);
    render(<App />);

    expect(await screen.findByRole("heading", { name: tab.heading, level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: tab.link })).toHaveAttribute("aria-current", "page");
  });
}

test("there is no Orders nav button — the booking chain lives only on Basket", () => {
  render(<App />);

  expect(screen.queryByRole("link", { name: "Orders" })).not.toBeInTheDocument();
});

test("the retired /orders path redirects to the Basket booking home", async () => {
  window.history.pushState({}, "", "/orders");
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Basket Builder" })).toBeInTheDocument();
  await waitFor(() => expect(window.location.pathname).toBe("/basket"));
});

test("risk scenarios is directly addressable", async () => {
  window.history.pushState({}, "", "/risk");
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Risk Scenarios" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Risk Scenarios" })).toHaveAttribute(
    "aria-current",
    "page",
  );
});
