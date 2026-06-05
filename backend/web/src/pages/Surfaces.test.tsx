import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { SurfacesPage } from "./Surfaces";
import { SURFACE_EMPTY, SURFACE_TWO_SLICES } from "../test/fixtures";
import { mockFetch } from "../test/http";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows a loading state before data arrives", async () => {
  mockFetch(SURFACE_TWO_SLICES);
  render(<SurfacesPage />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading");
  // Flush the pending fetch so the post-resolve state update happens inside act().
  await screen.findByRole("table");
});

test("renders one row per fitted slice with the SVI parameters", async () => {
  mockFetch(SURFACE_TWO_SLICES);
  render(<SurfacesPage />);

  // Caption reflects the slice count; both maturities render.
  expect(await screen.findByText(/2 fitted maturities/)).toBeInTheDocument();
  expect(screen.getByText("0.250")).toBeInTheDocument();
  expect(screen.getByText("0.750")).toBeInTheDocument();
  // The first slice's b parameter (0.34 → "0.3400") is shown.
  expect(screen.getByText("0.3400")).toBeInTheDocument();
  // Two data rows in the body.
  const rows = screen.getAllByRole("row");
  expect(rows.length).toBe(1 + 2); // header + 2 slices
});

test("renders an empty-state message when no surface exists", async () => {
  mockFetch(SURFACE_EMPTY);
  render(<SurfacesPage />);
  expect(await screen.findByText(/No fitted surface/)).toBeInTheDocument();
});

test("renders a typed error, not a blank page, when the API fails", async () => {
  mockFetch({ error: "boom" }, false);
  render(<SurfacesPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent("Failed to load");
  });
});
