import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { IbkrStatus } from "../../api";
import { renderWithClient } from "../../test/renderWithClient";
import { jsonGet, server } from "../../test/server";
import { IbkrConnectionPanel } from "./IbkrConnectionPanel";

const NOT_CONFIGURED: IbkrStatus = {
  configured: false,
  authenticated: false,
  established: false,
  competing: false,
  account: null,
  detail:
    "IBKR gateway is not configured. Set IBKR_CP_GATEWAY=1 in the repo .env, then run scripts/ibkr_login.py from a shell to authenticate.",
};

const AUTHED_NOT_ESTABLISHED: IbkrStatus = {
  configured: true,
  authenticated: true,
  established: false,
  competing: false,
  account: null,
  detail:
    "Authenticated, but the brokerage session is not established yet. Click Open brokerage session.",
};

const READY: IbkrStatus = {
  configured: true,
  authenticated: true,
  established: true,
  competing: false,
  account: "DUQ574355",
  detail: "Session ready: authenticated and brokerage session established.",
};

test("not-configured state shows a warn pill and the scripts/ibkr_login.py hint as code", async () => {
  server.use(jsonGet("/api/ibkr/status", NOT_CONFIGURED));
  renderWithClient(<IbkrConnectionPanel />);

  expect(await screen.findByText("Gateway not configured")).toBeInTheDocument();
  // The shell command is surfaced as inline code, not buried in prose.
  const hint = screen.getByText("scripts/ibkr_login.py");
  expect(hint.tagName).toBe("CODE");
  // Cannot open a brokerage session before authenticating.
  expect(screen.getByRole("button", { name: /Open brokerage session/i })).toBeDisabled();
});

test("a ready session shows an ok pill and the resolved account", async () => {
  server.use(jsonGet("/api/ibkr/status", READY));
  renderWithClient(<IbkrConnectionPanel />);

  expect(await screen.findByText("Session ready")).toBeInTheDocument();
  expect(screen.getByText(/account DUQ574355/)).toBeInTheDocument();
  // Already established: nothing to open.
  expect(screen.getByRole("button", { name: /Open brokerage session/i })).toBeDisabled();
});

test("authenticated-but-not-established enables Open brokerage session and POSTs connect", async () => {
  let connected = false;
  server.use(
    // Before connect the gateway reports authenticated-not-established; after, the brokerage
    // session is established, exactly as the live BFF /status would report.
    http.get("/api/ibkr/status", () =>
      HttpResponse.json(connected ? READY : AUTHED_NOT_ESTABLISHED),
    ),
    http.post("/api/ibkr/connect", () => {
      connected = true;
      return HttpResponse.json(READY);
    }),
  );
  renderWithClient(<IbkrConnectionPanel />);

  const open = await screen.findByRole("button", { name: /Open brokerage session/i });
  expect(open).toBeEnabled();
  await userEvent.click(open);

  await waitFor(() => expect(connected).toBe(true));
  // The fresh status flows straight into the pill: the panel now reads ready.
  expect(await screen.findByText("Session ready")).toBeInTheDocument();
});

test("a 409 from connect surfaces the honest detail including the login hint", async () => {
  server.use(jsonGet("/api/ibkr/status", AUTHED_NOT_ESTABLISHED));
  server.use(
    http.post("/api/ibkr/connect", () =>
      HttpResponse.json(
        {
          error: "ibkr_not_authenticated",
          detail:
            "Gateway is up but not authenticated. A browser login does not run from the web app, run scripts/ibkr_login.py from a shell to log in.",
          login_hint: "! scripts/ibkr_login.py",
        },
        { status: 409 },
      ),
    ),
  );
  renderWithClient(<IbkrConnectionPanel />);

  const open = await screen.findByRole("button", { name: /Open brokerage session/i });
  await userEvent.click(open);

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/Could not open the brokerage session/);
  expect(alert).toHaveTextContent(/A browser login does not run from the web app/);
});

test("Refresh status re-fetches the gateway state", async () => {
  let hits = 0;
  server.use(
    http.get("/api/ibkr/status", () => {
      hits += 1;
      return HttpResponse.json(hits === 1 ? NOT_CONFIGURED : READY);
    }),
  );
  renderWithClient(<IbkrConnectionPanel />);

  expect(await screen.findByText("Gateway not configured")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Refresh status/i }));
  expect(await screen.findByText("Session ready")).toBeInTheDocument();
});
