import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("./components/Plot", async () => await import("./test/plotMock"));
vi.mock("./components/CandleChart", async () => await import("./test/candleMock"));
vi.mock("./components/LightweightLineChart", async () => await import("./test/lightweightLineMock"));

import { App } from "./App";
import { RECORDED_EMPTY } from "./test/fixtures";
import { jsonGet, server } from "./test/server";

// The shell tests want every tab in its labelled-empty state (the msw defaults serve the risk
// tab's empty surface already; recorded-dates defaults to two dates, so empty it here).
beforeEach(() => {
  server.use(jsonGet("/api/recorded-dates", RECORDED_EMPTY));
});

afterEach(() => {
  window.history.pushState({}, "", "/");
});

test("top navigation reaches Market, Risk Scenarios, and Orders", async () => {
  const user = userEvent.setup();
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Market" })).toBeInTheDocument();

  await user.click(screen.getByRole("link", { name: "Risk Scenarios" }));
  expect(await screen.findByRole("heading", { name: "Risk Scenarios" })).toBeInTheDocument();
  await waitFor(() => expect(window.location.pathname).toBe("/risk"));

  await user.click(screen.getByRole("link", { name: "Orders" }));
  expect(await screen.findByRole("heading", { name: "Orders" })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/orders");
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
