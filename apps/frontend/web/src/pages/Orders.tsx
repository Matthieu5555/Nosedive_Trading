// Tab 3 — Orders (execution sketch, roadmap Phase 3). Antho's ticket / preview / history
// layout, restored verbatim, but READ-ONLY: execution is explicitly out of scope until an
// owner gate (the orders/paper-trading backend was dropped on purpose). Nothing here calls the
// network — the ticket is local state, the preview is an indicative client-side estimate, and
// the history is an empty sketch state. The "Submit" action is disabled and self-labels why.

import { useState } from "react";

import { Metric } from "../components/Metric";
import { money, number } from "../lib/format";

interface OrderTicket {
  side: "buy" | "sell";
  symbol: string;
  quantity: number;
  limit_price: number;
  expiry: string;
  strike: number;
  option_type: "call" | "put";
}

const defaultTicket: OrderTicket = {
  side: "buy",
  symbol: "SPX",
  quantity: 2,
  limit_price: 47.5,
  expiry: "2026-06-19",
  strike: 5350,
  option_type: "call",
};

// The contract multiplier is the only assumption the indicative preview makes; it is labelled
// as indicative so no one mistakes it for a priced, risk-checked order.
const CONTRACT_MULTIPLIER = 100;

export function OrdersPage() {
  const [ticket, setTicket] = useState<OrderTicket>(defaultTicket);
  const notional = ticket.quantity * ticket.limit_price * CONTRACT_MULTIPLIER;

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Paper execution</p>
          <h1>Orders</h1>
        </div>
        <div className="session-pill">Order ticket</div>
      </div>

      <div className="sketch-banner">
        Execution sketch — read-only. Building a ticket, signing it, and sending orders is roadmap
        Phase 3 and is not wired to any broker. Nothing on this tab leaves the browser.
      </div>

      <div className="orders-grid">
        <article className="panel ticket-panel">
          <div className="panel-heading">
            <h2>Ticket</h2>
            <span className="status">{ticket.symbol}</span>
          </div>
          <form className="ticket-form" onSubmit={(event) => event.preventDefault()}>
            <label>
              Side
              <select
                value={ticket.side}
                onChange={(event) =>
                  setTicket({ ...ticket, side: event.target.value as OrderTicket["side"] })
                }
              >
                <option value="buy">Buy</option>
                <option value="sell">Sell</option>
              </select>
            </label>
            <label>
              Symbol
              <input
                value={ticket.symbol}
                onChange={(event) =>
                  setTicket({ ...ticket, symbol: event.target.value.toUpperCase() })
                }
              />
            </label>
            <label>
              Qty
              <input
                type="number"
                min={1}
                value={ticket.quantity}
                onChange={(event) =>
                  setTicket({ ...ticket, quantity: Number(event.target.value) })
                }
              />
            </label>
            <label>
              Limit
              <input
                type="number"
                min={0.05}
                step={0.05}
                value={ticket.limit_price}
                onChange={(event) =>
                  setTicket({ ...ticket, limit_price: Number(event.target.value) })
                }
              />
            </label>
            <label>
              Expiry
              <input
                type="date"
                value={ticket.expiry}
                onChange={(event) => setTicket({ ...ticket, expiry: event.target.value })}
              />
            </label>
            <label>
              Strike
              <input
                type="number"
                step={5}
                value={ticket.strike}
                onChange={(event) => setTicket({ ...ticket, strike: Number(event.target.value) })}
              />
            </label>
            <label>
              Type
              <select
                value={ticket.option_type}
                onChange={(event) =>
                  setTicket({ ...ticket, option_type: event.target.value as OrderTicket["option_type"] })
                }
              >
                <option value="call">Call</option>
                <option value="put">Put</option>
              </select>
            </label>
            <div className="ticket-actions">
              <button type="submit" disabled title="Execution is a roadmap Phase 3 sketch">
                Submit (sketch — disabled)
              </button>
            </div>
          </form>
        </article>

        <article className="panel preview-panel">
          <div className="panel-heading">
            <h2>Preview</h2>
            <span className="status">indicative</span>
          </div>
          <div className="preview-grid">
            <Metric label="Side" value={ticket.side.toUpperCase()} />
            <Metric
              label="Contract"
              value={`${ticket.symbol} ${number(ticket.strike, 0)} ${ticket.option_type.toUpperCase()}`}
            />
            <Metric label="Qty" value={number(ticket.quantity, 0)} />
            <Metric label="Limit" value={money(ticket.limit_price)} />
            <Metric label="Notional (×100)" value={money(notional, "USD", 0)} />
            <Metric label="Expiry" value={ticket.expiry} />
          </div>
          <p>Indicative only — no pricing, no risk check, no greeks. Not a live preview.</p>
        </article>

        <article className="panel history-panel">
          <div className="panel-heading">
            <h2>History</h2>
            <span className="status">sketch</span>
          </div>
          <p>No orders. Execution is not wired (roadmap Phase 3).</p>
        </article>
      </div>
    </section>
  );
}
