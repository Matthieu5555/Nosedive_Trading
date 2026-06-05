import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { RiskPage } from "./Risk";
import { RISK_TWO_GROUPS } from "../test/fixtures";
import { mockFetch } from "../test/http";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("renders aggregate groups with their net Greeks", async () => {
  mockFetch(RISK_TWO_GROUPS);
  render(<RiskPage />);

  expect(await screen.findByText(/2 aggregate groups/)).toBeInTheDocument();
  expect(screen.getByText("AAPL")).toBeInTheDocument();
  expect(screen.getByText("MSFT")).toBeInTheDocument();
  // net_delta 123.45 → "123.45"; -50.0 → "-50.00".
  expect(screen.getByText("123.45")).toBeInTheDocument();
  expect(screen.getByText("-50.00")).toBeInTheDocument();
});

test("shows an empty-state when no aggregates are persisted", async () => {
  mockFetch({ portfolio_id: null, n_aggregates: 0, aggregates: [] });
  render(<RiskPage />);
  expect(await screen.findByText(/No risk aggregates/)).toBeInTheDocument();
});

test("renders a typed error when the API fails", async () => {
  mockFetch({ error: "boom" }, false);
  render(<RiskPage />);
  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent("Failed to load");
  });
});
