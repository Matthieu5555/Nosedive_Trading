import { useEffect, useMemo, useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";

import type { BasketLegInput, DeltaBandsResponse, IndicesResponse } from "../api";
import { buildTemplate, TEMPLATE_LABELS, type TemplateName } from "../basketTemplates";
import { BasketLegGrid } from "../components/BasketLegGrid";
import { TicketPanel } from "../components/TicketPanel";
import { useFetch } from "../hooks/useFetch";
import { BacktestSection } from "./ordres/BacktestSection";
import { BrokerReconciliation } from "./ordres/BrokerReconciliation";

const TEMPLATES: TemplateName[] = ["straddle", "strangle", "risk_reversal"];

export function OrdresPage() {
  const indices = useFetch<IndicesResponse>("/api/indices");
  const indexOptions = useMemo(() => indices.data?.indices ?? [], [indices.data]);
  const deltaBands = useFetch<DeltaBandsResponse>("/api/config/delta-bands");

  const [underlying, setUnderlying] = useState("");
  const [tradeDate, setTradeDate] = useState("");
  const [tenor, setTenor] = useState("1m");
  const [legs, setLegs] = useState<BasketLegInput[]>([]);

  useEffect(() => {
    if (indexOptions.length === 0) return;
    if (!underlying || !indexOptions.some((o) => o.symbol === underlying)) {
      setUnderlying(indexOptions[0].symbol);
    }
  }, [indexOptions, underlying]);

  function addLeg(leg: BasketLegInput) {
    setLegs((current) => [...current, leg]);
  }
  function removeLeg(index: number) {
    setLegs((current) => current.filter((_, i) => i !== index));
  }
  function applyTemplate(name: TemplateName) {
    setLegs(buildTemplate(name, underlying, tenor));
  }

  const basketId = `basket-${underlying}-${tradeDate || "latest"}`;

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">From the book to the broker — ticket, send, reconcile, backtest</p>
          <h1>Ordres</h1>
        </div>
      </div>
      <p>
        Turn the composed book into an order ticket, see where it would be sent (paper only —
        live sending is off), reconcile the booked fills against the broker, and backtest the line
        that drives it. Top to bottom: <strong>ticket → send → reconcile → backtest</strong>.
      </p>

      {indices.error !== null && (
        <p role="alert" className="error">
          Could not load the index list: {indices.error}
        </p>
      )}
      {deltaBands.error !== null && (
        <p role="alert" className="error">
          Could not load the delta-band axis: {deltaBands.error}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Order ticket</CardTitle>
          <CardDescription>
            Compose the legs of the book, then preview the order ticket. The ticket is the object a
            live send would sign — nothing is transmitted here.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="basket-controls">
            <label>
              Underlying{" "}
              <select
                aria-label="underlying"
                value={underlying}
                disabled={indexOptions.length === 0}
                onChange={(e) => setUnderlying(e.target.value)}
              >
                {indexOptions.map((item) => (
                  <option key={item.symbol} value={item.symbol}>
                    {item.name} ({item.symbol})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Trade date (empty = latest){" "}
              <input
                aria-label="trade date"
                type="date"
                value={tradeDate}
                onChange={(e) => setTradeDate(e.target.value)}
              />
            </label>
            <label>
              Tenor{" "}
              <input aria-label="tenor" value={tenor} onChange={(e) => setTenor(e.target.value)} />
            </label>
          </div>

          <div className="basket-templates" role="group" aria-label="templates">
            {TEMPLATES.map((name) => (
              <button
                key={name}
                type="button"
                aria-label={`template ${name}`}
                onClick={() => applyTemplate(name)}
              >
                {TEMPLATE_LABELS[name]}
              </button>
            ))}
          </div>

          <BasketLegGrid
            legs={legs}
            defaultUnderlying={underlying}
            defaultTenor={tenor}
            bands={deltaBands.data?.delta_bands ?? []}
            onAdd={addLeg}
            onRemove={removeLeg}
          />

          {legs.length > 0 && (
            <TicketPanel
              basketId={basketId}
              underlying={underlying}
              tradeDate={tradeDate}
              legs={legs}
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Send &amp; status</CardTitle>
          <CardDescription>
            Sending orders to a live broker is turned off. Tickets you book here go to the paper
            book only — nothing reaches a broker.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <article className="panel" aria-label="Order transmission">
            <p>
              Live sending is <strong>off</strong>. Booking a ticket above writes only to the{" "}
              <strong>paper</strong> book — it never reaches a broker; the “Send to broker” control
              on the ticket stays disabled by design.
            </p>
            <span className="status">Paper only · live sending is off</span>
          </article>
        </CardContent>
      </Card>

      <BrokerReconciliation />

      <Card>
        <CardHeader>
          <CardTitle>Backtest</CardTitle>
          <CardDescription>
            Validate the line over the days you have captured — cumulative P&amp;L and the by-Greek
            attribution.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <BacktestSection />
        </CardContent>
      </Card>
    </section>
  );
}
