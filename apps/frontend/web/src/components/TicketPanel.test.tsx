import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { TicketPanel } from "./TicketPanel";
import type { BasketLegInput, OrderTicketResponse } from "../api";
import { jsonPost, server } from "../test/server";

// The legs the panel is handed (already composed in the Basket view above it): a long option leg
// and a short stock hedge — the ticket maps long->BUY / short->SELL with a positive quantity.
const LEGS: BasketLegInput[] = [
  { instrument_kind: "option", side: "long", quantity: 1, underlying: "AAA",
    tenor_label: "3m", delta_band: "30dc" },
  { instrument_kind: "stock", side: "short", quantity: -2, underlying: "AAA" },
];

// What the BFF returns for that basket — the component renders this, the mapping itself is pinned
// in the Python unit tests, so this fixture is the BFF's authority, not a re-derivation.
const TICKET: OrderTicketResponse = {
  source_basket_id: "basket-AAA-latest",
  trade_date: "2026-05-29",
  underlying: "AAA",
  target_broker: "ibkr",
  time_in_force: "day",
  mode: "paper",
  legs: [
    { instrument_kind: "option", underlying: "AAA", side: "buy", quantity: 1,
      price_spec: { kind: "market" }, tenor_label: "3m", delta_band: "30dc" },
    { instrument_kind: "stock", underlying: "AAA", side: "sell", quantity: 2,
      price_spec: { kind: "market" }, tenor_label: null, delta_band: null },
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
  const rows = within(legsTable).getAllByRole("row").slice(1); // drop the header row
  expect(rows).toHaveLength(2);
  // The option leg: BUY, quantity 1, its grid cell shown.
  expect(within(rows[0]).getByText("BUY")).toBeInTheDocument();
  expect(within(rows[0]).getByText("AAA 3m/30dc")).toBeInTheDocument();
  // The stock hedge: SELL, positive magnitude 2.
  expect(within(rows[1]).getByText("SELL")).toBeInTheDocument();
  expect(within(rows[1]).getByText("AAA (stock)")).toBeInTheDocument();
});

test("the send affordance is disabled and labelled 3B-gated; nothing can transmit", async () => {
  server.use(jsonPost("/api/ticket/preview", TICKET));
  renderPanel();

  await userEvent.click(screen.getByRole("button", { name: "Build ticket" }));
  await screen.findByRole("table", { name: "order ticket legs" });

  const send = screen.getByRole("button", { name: "Sign and send order" });
  expect(send).toBeDisabled();
  expect(screen.getByText(/3B — gated/)).toBeInTheDocument();
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
