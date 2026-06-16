import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import type { BookGreeks } from "../api";
import { BookSummary } from "./BookSummary";

const BOOK: BookGreeks = {
  delta: { dollar: -1250, unit: "$ per $1 of underlying" },
  gamma: { dollar: 36.5, unit: "$ per 1% move" },
  vega: { dollar: 410, unit: "$ per 1 vol point" },
  theta: { dollar: -18.25, unit: "$ per calendar day" },
  rho: { dollar: 7.5, unit: "$ per 1% rate" },
  market_value: 12500,
};

test("BookSummary shows the total market value and every dollar Greek with its unit", () => {
  render(<BookSummary book={BOOK} />);
  const table = screen.getByRole("table", { name: /Book dollar Greeks/i });

  expect(within(table).getByText("1.25 × 10⁴")).toBeInTheDocument();

  expect(within(table).getByText("-1.25 × 10³")).toBeInTheDocument();
  expect(within(table).getByText("3.65 × 10¹")).toBeInTheDocument();

  expect(within(table).getByText("$ per $1 of underlying")).toBeInTheDocument();
  expect(within(table).getByText("$ per 1 vol point")).toBeInTheDocument();
});

test("BookSummary re-currencies the unit strings for the index currency (€ for SX5E)", () => {
  render(<BookSummary book={BOOK} currency="€" />);
  const table = screen.getByRole("table", { name: /Book dollar Greeks/i });

  expect(within(table).getByText("€ per €1 of underlying")).toBeInTheDocument();
  expect(within(table).queryByText("$ per $1 of underlying")).not.toBeInTheDocument();
});
