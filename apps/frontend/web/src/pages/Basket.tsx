import { useState } from "react";

import type { BasketLegInput, BasketRiskResponse } from "../api";
import { priceBasket } from "../api";
import { BasketLegGrid } from "../components/BasketLegGrid";
import { BasketRiskPanel } from "../components/BasketRiskPanel";
import { buildTemplate, TEMPLATE_LABELS, type TemplateName } from "../basketTemplates";

const TEMPLATES: TemplateName[] = ["straddle", "strangle", "risk_reversal"];

// Compose a multi-leg basket and price/risk it off the Tab-1 analytics (WS 2A). The operator
// picks an underlying, trade date and tenor, builds legs (by hand or a one-click template), then
// prices the basket — the panel shows the book-additive dollar Greeks and the per-leg breakdown.
export function BasketPage() {
  const [underlying, setUnderlying] = useState("AAPL");
  const [tradeDate, setTradeDate] = useState("");
  const [tenor, setTenor] = useState("1m");
  const [legs, setLegs] = useState<BasketLegInput[]>([]);
  const [result, setResult] = useState<BasketRiskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function addLeg(leg: BasketLegInput) {
    setLegs((current) => [...current, leg]);
  }
  function removeLeg(index: number) {
    setLegs((current) => current.filter((_, i) => i !== index));
  }
  function applyTemplate(name: TemplateName) {
    setLegs(buildTemplate(name, underlying, tenor));
  }

  async function price() {
    setError(null);
    setLoading(true);
    try {
      const composed = await priceBasket({
        basket_id: `basket-${underlying}-${tradeDate || "latest"}`,
        trade_date: tradeDate,
        underlying,
        legs,
      });
      setResult(composed);
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Analytics</p>
          <h1>Basket Builder</h1>
        </div>
      </div>
      <p>
        Compose a multi-leg position and price it off the Tab-1 analytics. Every basket number is
        the book-additive sum of the per-position dollar Greeks — never a fresh pricing pass.
      </p>

      <div className="basket-controls">
        <label>
          Underlying{" "}
          <input aria-label="underlying" value={underlying}
            onChange={(e) => setUnderlying(e.target.value)} />
        </label>
        <label>
          Trade date{" "}
          <input aria-label="trade date" type="date" value={tradeDate}
            onChange={(e) => setTradeDate(e.target.value)} />
        </label>
        <label>
          Tenor{" "}
          <input aria-label="tenor" value={tenor}
            onChange={(e) => setTenor(e.target.value)} />
        </label>
      </div>

      <div className="basket-templates" role="group" aria-label="templates">
        {TEMPLATES.map((name) => (
          <button key={name} type="button" aria-label={`template ${name}`}
            onClick={() => applyTemplate(name)}>
            {TEMPLATE_LABELS[name]}
          </button>
        ))}
      </div>

      <BasketLegGrid
        legs={legs}
        defaultUnderlying={underlying}
        defaultTenor={tenor}
        onAdd={addLeg}
        onRemove={removeLeg}
      />

      <button type="button" onClick={price} disabled={loading || legs.length === 0}>
        {loading ? "Pricing…" : "Price basket"}
      </button>

      {error !== null && (
        <p role="alert" className="error">
          Failed to price basket: {error}
        </p>
      )}
      {result !== null && <BasketRiskPanel result={result} />}
    </section>
  );
}
