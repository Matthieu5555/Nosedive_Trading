import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useEffect } from "react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { describe, expect, test, vi } from "vitest";

import { jsonPost, server } from "../../test/server";
import type { GuideStep } from "./assistantApi";
import { AssistantProvider, useSetAssistantFrame } from "./AssistantContext";
import { FloatingAssistant } from "./FloatingAssistant";

// jsdom has no layout, so the real Spotlight (which reads getBoundingClientRect) would render an
// empty overlay. We mock it to a marker so the tests can assert "a tour is highlighting <id>" and
// "the spotlight is cleared" without depending on geometry, exactly as the contract suggests.
vi.mock("./Spotlight", () => ({
  Spotlight: ({ tourId }: { tourId: string | null }) =>
    tourId === null ? null : <div data-testid="spotlight" data-tour-id={tourId} />,
}));

const FRAME = {
  underlying: "SX5E",
  asOf: "2026-06-17",
  runId: "run-1",
  mode: "strict" as const,
  focusedElementId: null,
};

const STEP_NAV: GuideStep = {
  say: "Open the Basket page up top.",
  highlight: "nav.basket",
  expect: "navigate",
  done: false,
};

const STEP_DONE: GuideStep = {
  say: "That is the Basket page, you are all set.",
  highlight: null,
  expect: "none",
  done: true,
};

// A harness that seeds the surface frame (so the panel is "ready") and lets a test navigate the
// router from inside, mirroring a real route change. Optionally renders a node carrying a
// data-tour-id so the click path has a real element to bind to.
function Harness({ children }: { children?: React.ReactNode }) {
  const setFrame = useSetAssistantFrame();
  useEffect(() => {
    setFrame(FRAME);
  }, [setFrame]);
  return (
    <>
      {children}
      <FloatingAssistant />
    </>
  );
}

function NavButton() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate("/basket")}>
      go-basket
    </button>
  );
}

function renderApp(children?: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AssistantProvider>
        <Harness>{children}</Harness>
      </AssistantProvider>
    </MemoryRouter>,
  );
}

async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
}

describe("AssistantPanel chrome", () => {
  test("refresh clears the conversation turns", async () => {
    server.use(
      jsonPost("/api/assistant", {
        answer: "You are looking at the SX5E surface.",
        citations: [],
        grounded: true,
        frame: {
          underlying: "SX5E",
          trade_date: "2026-06-17",
          run_id: "run-1",
          mode: "strict",
          close_instant: "17:30 CET",
          coverage_label: null,
        },
      }),
    );
    const user = userEvent.setup();
    renderApp();
    await openPanel(user);
    await user.click(screen.getByRole("button", { name: "What am I looking at?" }));
    await screen.findByText(/SX5E surface/);

    await user.click(screen.getByRole("button", { name: "Clear the conversation" }));
    expect(screen.queryByText(/SX5E surface/)).toBeNull();
  });

  test("expand toggles the .assistant-panel--expanded class", async () => {
    const user = userEvent.setup();
    renderApp();
    await openPanel(user);
    const panel = screen.getByRole("complementary", { name: "Assistant" });
    expect(panel).not.toHaveClass("assistant-panel--expanded");

    await user.click(screen.getByRole("button", { name: "Expand the assistant" }));
    expect(panel).toHaveClass("assistant-panel--expanded");

    await user.click(screen.getByRole("button", { name: "Return to corner" }));
    expect(panel).not.toHaveClass("assistant-panel--expanded");
  });
});

describe("guide loop", () => {
  test("a 'how do i' question starts a tour and renders step.say with the spotlight", async () => {
    server.use(jsonPost("/api/assistant/guide", STEP_NAV));
    const user = userEvent.setup();
    renderApp();
    await openPanel(user);

    const input = screen.getByLabelText("Your question");
    await user.type(input, "How do I open the basket?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    // The step text shows as an assistant message, and the spotlight rings the named anchor.
    await screen.findByText("Open the Basket page up top.");
    const spotlight = await screen.findByTestId("spotlight");
    expect(spotlight).toHaveAttribute("data-tour-id", "nav.basket");
    // The user's goal is echoed into the thread.
    expect(screen.getByText("How do I open the basket?")).toBeInTheDocument();
  });

  test("the 'Show me how' affordance starts a tour", async () => {
    server.use(jsonPost("/api/assistant/guide", STEP_NAV));
    const user = userEvent.setup();
    renderApp();
    await openPanel(user);
    await user.click(screen.getByRole("button", { name: "Show me how" }));
    await screen.findByText("Open the Basket page up top.");
  });

  test("a navigate step advances on a route change and ends when done", async () => {
    // First call returns a navigate step; once the route changes, the loop requests again and we
    // return the done step.
    let calls = 0;
    server.use(
      http.post("/api/assistant/guide", () => {
        calls += 1;
        return HttpResponse.json(calls === 1 ? STEP_NAV : STEP_DONE);
      }),
    );
    const user = userEvent.setup();
    renderApp(<NavButton />);
    await openPanel(user);

    await user.click(screen.getByRole("button", { name: "Show me how" }));
    await screen.findByText("Open the Basket page up top.");
    expect(await screen.findByTestId("spotlight")).toBeInTheDocument();

    // Simulate the user navigating: the loop should detect the path change and request the next step.
    await user.click(screen.getByRole("button", { name: "go-basket" }));

    await screen.findByText(/you are all set/);
    // Done clears the spotlight and ends the loop, no dangling overlay.
    await waitFor(() => expect(screen.queryByTestId("spotlight")).toBeNull());
    expect(screen.queryByRole("button", { name: "Stop tour" })).toBeNull();
  });

  test("the manual Next button advances the tour", async () => {
    let calls = 0;
    server.use(
      http.post("/api/assistant/guide", () => {
        calls += 1;
        return HttpResponse.json(calls === 1 ? STEP_NAV : STEP_DONE);
      }),
    );
    const user = userEvent.setup();
    renderApp();
    await openPanel(user);
    await user.click(screen.getByRole("button", { name: "Show me how" }));
    await screen.findByText("Open the Basket page up top.");

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText(/you are all set/);
    await waitFor(() => expect(screen.queryByTestId("spotlight")).toBeNull());
  });

  test("a failing guide request surfaces a loud error and leaves no spotlight", async () => {
    server.use(
      http.post("/api/assistant/guide", () =>
        HttpResponse.json(
          { error: "assistant_unavailable", detail: "OpenRouter timed out" },
          { status: 502 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp();
    await openPanel(user);
    await user.click(screen.getByRole("button", { name: "Show me how" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Assistant unavailable/);
    expect(alert).toHaveTextContent(/OpenRouter timed out/);
    expect(screen.queryByTestId("spotlight")).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop tour" })).toBeNull();
  });

  test("Stop tour ends the loop and clears the spotlight", async () => {
    server.use(jsonPost("/api/assistant/guide", STEP_NAV));
    const user = userEvent.setup();
    renderApp();
    await openPanel(user);
    await user.click(screen.getByRole("button", { name: "Show me how" }));
    await screen.findByTestId("spotlight");

    await user.click(screen.getByRole("button", { name: "Stop tour" }));
    await waitFor(() => expect(screen.queryByTestId("spotlight")).toBeNull());
  });
});
