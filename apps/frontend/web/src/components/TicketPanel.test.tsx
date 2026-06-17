import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import type { BasketLegInput, OrderTicketResponse } from "../api";
import { jsonGet, jsonPost, server } from "../test/server";
import { TicketPanel } from "./TicketPanel";

const LEGS: BasketLegInput[] = [
  {
    instrument_kind: "option",
    side: "long",
    quantity: 1,
    underlying: "AAA",
    tenor_label: "3m",
    delta_band: "30dc",
  },
  { instrument_kind: "stock", side: "short", quantity: -2, underlying: "AAA" },
];

const TICKET: OrderTicketResponse = {
  source_basket_id: "basket-AAA-latest",
  trade_date: "2026-05-29",
  underlying: "AAA",
  target_broker: "ibkr",
  time_in_force: "day",
  mode: "paper",
  legs: [
    {
      instrument_kind: "option",
      underlying: "AAA",
      side: "buy",
      quantity: 1,
      price_spec: { kind: "market" },
      tenor_label: "3m",
      delta_band: "30dc",
    },
    {
      instrument_kind: "stock",
      underlying: "AAA",
      side: "sell",
      quantity: 2,
      price_spec: { kind: "market" },
      tenor_label: null,
      delta_band: null,
    },
  ],
  n_legs: 2,
  gated: { transmit: false, reason: "sign-and-send is behind an explicit owner gate" },
};

function renderPanel(legs: BasketLegInput[] = LEGS) {
  return render(
    <TicketPanel basketId="basket-AAA-latest" underlying="AAA" tradeDate="" legs={legs} />,
  );
}

test("builds a ticket and renders the mapped legs (long->BUY, short->SELL, magnitude qty)", async () => {
  server.use(jsonPost("/api/ticket/preview", TICKET));
  renderPanel();

  await userEvent.click(screen.getByRole("button", { name: "Build ticket" }));

  const legsTable = await screen.findByRole("table", { name: "order ticket legs" });
  const rows = within(legsTable).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);

  expect(within(rows[0]).getByText("BUY")).toBeInTheDocument();
  expect(within(rows[0]).getByText("AAA 3m/30dc")).toBeInTheDocument();

  expect(within(rows[1]).getByText("SELL")).toBeInTheDocument();
  expect(within(rows[1]).getByText("AAA (stock)")).toBeInTheDocument();
});

test("the send affordance is disabled and labelled live-sending-off; nothing can transmit", async () => {
  server.use(jsonPost("/api/ticket/preview", TICKET));
  renderPanel();

  await userEvent.click(screen.getByRole("button", { name: "Build ticket" }));
  await screen.findByRole("table", { name: "order ticket legs" });

  const send = screen.getByRole("button", { name: "Send order to broker" });
  expect(send).toBeDisabled();
  expect(screen.getByText(/Live sending is off/)).toBeInTheDocument();
});

test("a malformed-ticket 400 surfaces the labelled detail, not a blank panel", async () => {
  server.use(
    http.post("/api/ticket/preview", () =>
      HttpResponse.json({ error: "bad_ticket", detail: "unknown target broker" }, { status: 400 }),
    ),
  );
  renderPanel();

  await userEvent.click(screen.getByRole("button", { name: "Build ticket" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/unknown target broker/);
});

test("the build button is disabled when there are no legs to build from", () => {
  renderPanel([]);
  expect(screen.getByRole("button", { name: "Build ticket" })).toBeDisabled();
});

test("broker/TIF selectors are populated from the options endpoint, not a hardcoded list", async () => {
  server.use(
    jsonGet("/api/ticket/options", { brokers: ["ibkr"], time_in_force: ["day", "gtc", "ioc"] }),
  );
  renderPanel();
  expect(await screen.findByRole("option", { name: "IOC" })).toBeInTheDocument();
});

async function previewThenReveal() {
  server.use(jsonPost("/api/ticket/preview", TICKET));
  renderPanel();
  await userEvent.click(screen.getByRole("button", { name: "Build ticket" }));
  await screen.findByRole("table", { name: "order ticket legs" });
}

test("the Book button is disabled until a password is entered (the write barrier)", async () => {
  await previewThenReveal();
  const book = screen.getByRole("button", { name: "Book (paper)" });

  expect(book).toBeDisabled();
  await userEvent.type(screen.getByLabelText("booking password"), "secret-pw");
  expect(book).toBeEnabled();
});

test("a verified booking surfaces the committed fills", async () => {
  await previewThenReveal();
  server.use(
    jsonPost("/api/booking/commit", {
      decision: "commit",
      booking_id: "bkg-abc",
      fill_ids: ["bkg-abc-fill-0"],
      fill_count: 1,
    }),
  );
  await userEvent.type(screen.getByLabelText("booking password"), "right-pw");
  await userEvent.click(screen.getByRole("button", { name: "Book (paper)" }));

  const status = await screen.findByRole("status");
  expect(status).toHaveTextContent(/Booked: 1 fill\(s\) written/);
  expect(status).toHaveTextContent(/bkg-abc/);
});

test("a blocked booking surfaces the labelled reason, fail-closed", async () => {
  await previewThenReveal();
  server.use(
    jsonPost("/api/booking/commit", {
      decision: "block",
      booking_id: "bkg-xyz",
      reason: "wrong_password",
      detail: "the supplied booking password did not match",
    }),
  );
  await userEvent.type(screen.getByLabelText("booking password"), "nope");
  await userEvent.click(screen.getByRole("button", { name: "Book (paper)" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/wrong_password/);
  expect(alert).toHaveTextContent(/did not match/);
});
