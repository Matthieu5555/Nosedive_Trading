import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";

import { assertNeverBlank } from "../../test/assertNeverBlank";
import { jsonPost, server } from "../../test/server";
import type { AssistantResponse } from "./assistantApi";
import { AssistantPanel } from "./AssistantPanel";

const FRAME = {
  underlying: "SX5E",
  trade_date: "2026-06-17",
  run_id: "run-1",
  mode: "strict" as const,
  close_instant: "17:30 CET",
  coverage_label: "1,706/2,412 quotes",
};

const GROUNDED: AssistantResponse = {
  answer: "You are looking at the SX5E implied-vol surface at the close.",
  citations: [
    {
      id: "atm_level",
      label: "ATM level",
      value: "1.83 × 10⁻¹ Vol",
      source: "recorded signal · 3m",
    },
  ],
  grounded: true,
  frame: FRAME,
};

const HONEST_GAP: AssistantResponse = {
  answer: "That isn't in what the screen shows for this close, I won't make it up.",
  citations: [],
  grounded: false,
  frame: FRAME,
};

function open() {
  return userEvent.setup();
}

describe("AssistantPanel", () => {
  test("is closed by default, only a non-blocking launch button, never a modal wall", () => {
    const rendered = render(<AssistantPanel underlying="SX5E" asOf="2026-06-17" />);
    expect(screen.getByRole("button", { name: "Ask the assistant" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByRole("complementary", { name: "Assistant" })).toBeNull();
    assertNeverBlank(rendered);
  });

  test("with no frame yet, opening shows an affirmative empty state, not a blank box", async () => {
    const user = open();
    render(<AssistantPanel underlying="" asOf={null} />);
    await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/Choose an index and a close/);
  });

  test("a grounded answer renders the text, its citation value, and the provenance caption", async () => {
    server.use(jsonPost("/api/assistant", GROUNDED));
    const user = open();
    render(<AssistantPanel underlying="SX5E" asOf="2026-06-17" runId="run-1" mode="strict" />);
    await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
    await user.click(screen.getByRole("button", { name: "What am I looking at?" }));

    await screen.findByText(/SX5E implied-vol surface/);
    // The number is the citation lifted from the facts block, byte-identical to a scorecard.
    expect(screen.getByText("1.83 × 10⁻¹ Vol")).toBeInTheDocument();
    // Provenance caption agrees with the active frame: subject · 17:30 CET close · mode · coverage.
    const frame = screen.getByText(/SX5E · close 2026-06-17 17:30 CET · strict · 1,706\/2,412/);
    expect(frame).toBeInTheDocument();
    expect(frame).not.toHaveTextContent("22:00");
  });

  test("a grounded=false answer renders the honest-gap copy in a quiet status, NOT a number", async () => {
    server.use(jsonPost("/api/assistant", HONEST_GAP));
    const user = open();
    render(<AssistantPanel underlying="SX5E" asOf="2026-06-17" />);
    await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
    await user.click(screen.getByRole("button", { name: "What am I looking at?" }));

    const gap = await screen.findByText(/I won't make it up/);
    expect(gap).toHaveAttribute("role", "status");
    // No citation list, so no number can leak.
    expect(screen.queryByRole("list", { name: "Citations" })).toBeNull();
  });

  test("a BFF/OpenRouter failure surfaces a LOUD inline error (role=alert), never silent", async () => {
    server.use(
      http.post("/api/assistant", () =>
        HttpResponse.json(
          { error: "assistant_unavailable", detail: "OpenRouter timed out" },
          { status: 502 },
        ),
      ),
    );
    const user = open();
    render(<AssistantPanel underlying="SX5E" asOf="2026-06-17" />);
    await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
    await user.click(screen.getByRole("button", { name: "What am I looking at?" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Assistant unavailable/);
    expect(alert).toHaveTextContent(/OpenRouter timed out/);
  });

  test("shows a thinking indicator while a request is in flight", async () => {
    let resolve!: (r: AssistantResponse) => void;
    const pending = new Promise<AssistantResponse>((r) => {
      resolve = r;
    });
    server.use(http.post("/api/assistant", async () => HttpResponse.json(await pending)));
    const user = open();
    render(<AssistantPanel underlying="SX5E" asOf="2026-06-17" />);
    await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
    await user.click(screen.getByRole("button", { name: "What am I looking at?" }));

    await waitFor(() =>
      expect(screen.getByText(/The assistant is thinking…/)).toHaveAttribute("aria-busy", "true"),
    );
    resolve(GROUNDED);
    await screen.findByText(/SX5E implied-vol surface/);
  });

  test("the 'what is this' shortcut is disabled with no focused element and labelled when set", async () => {
    server.use(jsonPost("/api/assistant", GROUNDED));
    const user = open();
    const { rerender } = render(<AssistantPanel underlying="SX5E" asOf="2026-06-17" />);
    await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
    expect(screen.getByRole("button", { name: "What's this?" })).toBeDisabled();

    rerender(<AssistantPanel underlying="SX5E" asOf="2026-06-17" focusedElementId="smile" />);
    const explainBtn = screen.getByRole("button", { name: /What is: Smile/ });
    expect(explainBtn).toBeEnabled();
    await user.click(explainBtn);
    await screen.findByText(/SX5E implied-vol surface/);
    // The asked question names the element via the shared copy map's label.
    expect(screen.getByText(/What is Smile\?/)).toBeInTheDocument();
  });

  test("the panel closes back to the launch button", async () => {
    const user = open();
    render(<AssistantPanel underlying="SX5E" asOf="2026-06-17" />);
    await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
    await user.click(screen.getByRole("button", { name: "Close the assistant" }));
    expect(screen.getByRole("button", { name: "Ask the assistant" })).toBeInTheDocument();
  });
});
