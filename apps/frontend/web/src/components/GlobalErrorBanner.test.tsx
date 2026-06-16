import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { reportRuntimeError } from "../lib/runtimeErrors";
import { GlobalErrorBanner } from "./GlobalErrorBanner";

// resetRuntimeErrorsForTests() runs in the global afterEach (src/test/setup.ts).

test("renders nothing while the surface is empty", () => {
  const { container } = render(<GlobalErrorBanner />);
  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("surfaces a reported failure as a visible alert", async () => {
  render(<GlobalErrorBanner />);
  // A failure reported after mount (the realistic case) shows up via the subscription.
  reportRuntimeError("500 BFF unreachable");
  expect(await screen.findByRole("alert")).toHaveTextContent("500 BFF unreachable");
});

test("dismissing a notice removes it from the banner", async () => {
  render(<GlobalErrorBanner />);
  reportRuntimeError("transient blip");
  expect(await screen.findByText("transient blip")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Dismiss error" }));
  expect(screen.queryByText("transient blip")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
