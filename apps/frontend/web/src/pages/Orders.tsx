import { FormEvent, useState } from "react";

import type { OrderHistoryItem, OrderPreview, OrdersDashboard, OrderTicket } from "../api";
import { postJson } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { Metric } from "../components/Metric";
import { useFetch } from "../hooks/useFetch";
import { money, number, statusLabel } from "../lib/format";

const defaultTicket: OrderTicket = {
  side: "buy",
  symbol: "SPX",
  quantity: 2,
  limit_price: 47.5,
  instrument_type: "index_option",
  expiry: "2026-06-19",
  strike: 5350,
  option_type: "call",
  time_in_force: "day",
};

export function OrdersPage() {
  const orders = useFetch<OrdersDashboard>("/api/orders");
  const [ticket, setTicket] = useState<OrderTicket>(defaultTicket);
  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [accepted, setAccepted] = useState<OrderHistoryItem | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function previewTicket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const next = await postJson<OrderPreview>("/api/orders/preview", ticket);
      setPreview(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  }

  async function submitTicket() {
    setError(null);
    try {
      const order = await postJson<OrderHistoryItem>("/api/orders", ticket);
      setAccepted(order);
      void orders.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    }
  }

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Paper execution</p>
          <h1>Orders</h1>
        </div>
        <div className="session-pill">Order ticket</div>
      </div>

      {error && <div className="state-panel state-panel-error">{error}</div>}

      <div className="orders-grid">
        <article className="panel ticket-panel">
          <div className="panel-heading">
            <h2>Ticket</h2>
            <span className="status">{ticket.symbol}</span>
          </div>
          <form className="ticket-form" onSubmit={previewTicket}>
            <label>
              Side
              <select
                value={ticket.side}
                onChange={(event) => setTicket({ ...ticket, side: event.target.value as OrderTicket["side"] })}
              >
                <option value="buy">Buy</option>
                <option value="sell">Sell</option>
              </select>
            </label>
            <label>
              Symbol
              <input
                value={ticket.symbol}
                onChange={(event) => setTicket({ ...ticket, symbol: event.target.value.toUpperCase() })}
              />
            </label>
            <label>
              Qty
              <input
                type="number"
                min={1}
                value={ticket.quantity}
                onChange={(event) => setTicket({ ...ticket, quantity: Number(event.target.value) })}
              />
            </label>
            <label>
              Limit
              <input
                type="number"
                min={0.05}
                step={0.05}
                value={ticket.limit_price}
                onChange={(event) => setTicket({ ...ticket, limit_price: Number(event.target.value) })}
              />
            </label>
            <label>
              Expiry
              <input
                type="date"
                value={ticket.expiry ?? ""}
                onChange={(event) => setTicket({ ...ticket, expiry: event.target.value })}
              />
            </label>
            <label>
              Strike
              <input
                type="number"
                step={5}
                value={ticket.strike ?? 0}
                onChange={(event) => setTicket({ ...ticket, strike: Number(event.target.value) })}
              />
            </label>
            <label>
              Type
              <select
                value={ticket.option_type ?? "call"}
                onChange={(event) =>
                  setTicket({ ...ticket, option_type: event.target.value as OrderTicket["option_type"] })
                }
              >
                <option value="call">Call</option>
                <option value="put">Put</option>
              </select>
            </label>
            <div className="ticket-actions">
              <button type="submit">Preview</button>
              <button type="button" onClick={submitTicket}>
                Submit
              </button>
            </div>
          </form>
        </article>

        <article className="panel preview-panel">
          <div className="panel-heading">
            <h2>Preview</h2>
            <span className="status">
              {statusLabel((preview ?? orders.data?.recent_preview)?.risk_check ?? "pending")}
            </span>
          </div>
          {(preview ?? orders.data?.recent_preview) && (
            <Preview preview={(preview ?? orders.data?.recent_preview) as OrderPreview} />
          )}
          {accepted && <div className="accepted-banner">{accepted.order_id} accepted</div>}
        </article>

        <article className="panel history-panel">
          <div className="panel-heading">
            <h2>History</h2>
            <span className="status">{orders.data?.mode ?? "paper"}</span>
          </div>
          <AsyncBlock loading={orders.loading} error={orders.error}>
            {orders.data && <History rows={orders.data.history} />}
          </AsyncBlock>
        </article>
      </div>
    </section>
  );
}

function Preview({ preview }: { preview: OrderPreview }) {
  return (
    <div className="preview-grid">
      <Metric label="Notional" value={money(preview.estimated_notional, "USD", 0)} />
      <Metric label="Commission" value={money(preview.estimated_commission)} />
      <Metric label="Delta" value={number(preview.greek_impact.delta, 3)} />
      <Metric label="Gamma" value={number(preview.greek_impact.gamma, 5)} />
      <Metric label="Vega" value={number(preview.greek_impact.vega, 2)} />
      <Metric label="Theta" value={number(preview.greek_impact.theta, 2)} />
    </div>
  );
}

function History({ rows }: { rows: OrderHistoryItem[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Order</th>
            <th>Side</th>
            <th>Contract</th>
            <th>Qty</th>
            <th>Status</th>
            <th>Avg</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.order_id}>
              <td>{row.order_id}</td>
              <td>{row.ticket.side.toUpperCase()}</td>
              <td>
                {row.ticket.symbol} {row.ticket.strike} {row.ticket.option_type?.toUpperCase()}
              </td>
              <td>{row.ticket.quantity}</td>
              <td>{statusLabel(row.status)}</td>
              <td>{row.average_price === null ? "—" : money(row.average_price)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
