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
