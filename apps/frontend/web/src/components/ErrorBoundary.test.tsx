import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

let shouldThrow = true;
function Boom() {
  if (shouldThrow) throw new Error("plotly choked on a NaN cell");
  return <div>healthy panel</div>;
}

beforeEach(() => {
  shouldThrow = true;

  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

test("renders children unchanged when nothing throws", () => {
  shouldThrow = false;
  render(
    <ErrorBoundary label="Risk surface">
      <Boom />
    </ErrorBoundary>,
  );
  expect(screen.getByText("healthy panel")).toBeInTheDocument();
});

test("a render error degrades to a labelled tile carrying the panel name and message", () => {
  render(
    <ErrorBoundary label="Risk surface">
      <Boom />
    </ErrorBoundary>,
  );
  const tile = screen.getByRole("alert");
  expect(tile).toHaveTextContent("Risk surface failed to render.");
  expect(tile).toHaveTextContent("plotly choked on a NaN cell");
});

test("Retry clears the error so a recovered child renders again", async () => {
  render(
    <ErrorBoundary label="Risk surface">
      <Boom />
    </ErrorBoundary>,
  );
  expect(screen.getByRole("alert")).toBeInTheDocument();

  shouldThrow = false;
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(screen.getByText("healthy panel")).toBeInTheDocument();
});
