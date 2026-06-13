// Order-ticket panel (WS 3A): build and preview an order ticket from the composed basket.
//
// Preview/build ONLY — paper/read-only. There is no code path from this panel to a broker: the
// "Sign & send" affordance is permanently disabled and labelled "3B — gated", and the previewed
// ticket carries the BFF's explicit `gated.transmit=false`. The operator picks the target broker,
// the time-in-force and a price spec (market, or limit with a price); the legs (side/quantity)
// come from the basket already composed above — the ticket maps long/short to BUY/SELL and shows
// a positive magnitude quantity. Sending is WS 3B, behind an explicit owner gate.

import { useState } from "react";

import type { BasketLegInput, OrderTicketLeg, OrderTicketResponse, TicketPriceSpec } from "../api";
import { previewTicket } from "../api";

const BROKERS = ["ibkr"] as const;
const TIFS = ["day", "gtc"] as const;

function legInstrument(leg: OrderTicketLeg): string {
  return leg.instrument_kind === "stock"
    ? `${leg.underlying} (stock)`
    : `${leg.underlying} ${leg.tenor_label}/${leg.delta_band}`;
}

function legPrice(spec: TicketPriceSpec): string {
  return spec.kind === "limit" ? `limit ${spec.price}` : "market";
}

interface TicketPanelProps {
  basketId: string;
  underlying: string;
  tradeDate: string;
  legs: BasketLegInput[];
}

export function TicketPanel({ basketId, underlying, tradeDate, legs }: TicketPanelProps) {
  const [broker, setBroker] = useState<string>("ibkr");
  const [tif, setTif] = useState<string>("day");
  const [priceKind, setPriceKind] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState<string>("");
  const [ticket, setTicket] = useState<OrderTicketResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function build() {
    setError(null);
    setLoading(true);
    const price_spec: TicketPriceSpec =
      priceKind === "limit" ? { kind: "limit", price: Number(limitPrice) } : { kind: "market" };
    try {
      setTicket(
        await previewTicket({
          basket_id: basketId,
          underlying,
          trade_date: tradeDate,
          target_broker: broker,
          time_in_force: tif,
          price_spec,
          legs,
        }),
      );
    } catch (err) {
      setTicket(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="panel ticket-panel" aria-label="Order ticket">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">Execution</p>
          <h2>Order ticket</h2>
        </div>
        <span className="status">preview · paper</span>
      </div>
      <p>
        Build an order ticket from the composed basket above. <strong>Preview only</strong> — the
        ticket is the object 3B will sign and send; nothing is transmitted here.
      </p>

      <div className="ticket-controls">
        <label>
          Broker{" "}
          <select aria-label="broker" value={broker} onChange={(e) => setBroker(e.target.value)}>
            {BROKERS.map((b) => (
              <option key={b} value={b}>
                {b.toUpperCase()}
              </option>
            ))}
          </select>
        </label>
        <label>
          Time in force{" "}
          <select aria-label="time in force" value={tif} onChange={(e) => setTif(e.target.value)}>
            {TIFS.map((t) => (
              <option key={t} value={t}>
                {t.toUpperCase()}
              </option>
            ))}
          </select>
        </label>
        <label>
          Price{" "}
          <select
            aria-label="price type"
            value={priceKind}
            onChange={(e) => setPriceKind(e.target.value as "market" | "limit")}
          >
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </select>
        </label>
        {priceKind === "limit" && (
          <label>
            Limit price{" "}
            <input
              aria-label="limit price"
              type="number"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
            />
          </label>
        )}
      </div>

      <button type="button" onClick={build} disabled={loading || legs.length === 0}>
        {loading ? "Building…" : "Build ticket"}
      </button>

      {error !== null && (
        <p role="alert" className="error">
          Failed to build ticket: {error}
        </p>
      )}

      {ticket !== null && (
        <>
          <table aria-label="order ticket legs">
            <caption>
              Ticket — {ticket.source_basket_id} → {ticket.target_broker.toUpperCase()} (
              {ticket.time_in_force.toUpperCase()}, {ticket.mode})
            </caption>
            <thead>
              <tr>
                <th>Side</th>
                <th>Qty</th>
                <th>Instrument</th>
                <th>Price</th>
              </tr>
            </thead>
            <tbody>
              {ticket.legs.map((leg, index) => (
                <tr key={index} aria-label={`${leg.side} ${leg.quantity} ${legInstrument(leg)}`}>
                  <td>{leg.side.toUpperCase()}</td>
                  <td>{leg.quantity}</td>
                  <td>{legInstrument(leg)}</td>
                  <td>{legPrice(leg.price_spec)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="ticket-gate" role="note" aria-label="transmission gate">
            <button type="button" disabled aria-label="Sign and send order">
              Sign &amp; send
            </button>
            <span>3B — gated: {ticket.gated.reason}</span>
          </div>
        </>
      )}
    </section>
  );
}
