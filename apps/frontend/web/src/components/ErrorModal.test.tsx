import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { reportRuntimeError } from "../lib/runtimeErrors";
import { ErrorModal } from "./ErrorModal";

// resetRuntimeErrorsForTests() runs in the global afterEach (src/test/setup.ts).

test("renders nothing while the surface is empty", () => {
  const { container } = render(<ErrorModal />);
  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

test("surfaces a reported failure as a centered alert dialog", async () => {
  render(<ErrorModal />);
  reportRuntimeError("500 BFF unreachable");
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("500 BFF unreachable");
  // The Dismiss control lives inside the same card (directly below the message), not a top strip.
  expect(screen.getByRole("button", { name: "Dismiss error" })).toBeInTheDocument();
});

test("dismissing the top notice reveals the next, then clears", async () => {
  render(<ErrorModal />);
  reportRuntimeError("first outage");
  reportRuntimeError("second outage");
  // Newest on top; a "+1 more" note summarises the older one.
  expect(await screen.findByText("second outage")).toBeInTheDocument();
  expect(screen.getByText(/1 earlier error/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Dismiss error" }));
  expect(screen.getByText("first outage")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Dismiss error" }));
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});
