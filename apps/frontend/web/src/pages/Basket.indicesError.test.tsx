import { render, screen } from "@testing-library/react";
import { http } from "msw";
import { expect, test, vi } from "vitest";

// Plotly draws to a canvas jsdom does not implement; swap the wrapper for the DOM stub.
vi.mock("../components/Plot", async () => await import("../test/plotMock"));

import { notMocked, server } from "../test/server";
import { BasketPage } from "./Basket";

// The Basket page's registry-driven inputs — the underlying list (/api/indices) and the delta-band
// axis (/api/config/delta-bands) — used to fail silently: the dropdown just disabled and the leg
// grid lost its bands with no word why. Each failure must now front a visible alert.

test("a failing /api/indices is surfaced, not silently swallowed into a dead dropdown", async () => {
  server.use(http.get("/api/indices", () => notMocked()));

  render(<BasketPage />);

  expect(await screen.findByText(/Could not load the index list/)).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "underlying" })).toBeDisabled();
});

test("a failing /api/config/delta-bands is surfaced", async () => {
  server.use(http.get("/api/config/delta-bands", () => notMocked()));

  render(<BasketPage />);

  expect(await screen.findByText(/Could not load the delta-band axis/)).toBeInTheDocument();
});
