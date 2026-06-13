import { useEffect, useMemo, useState } from "react";

import type { BasketLegInput, BasketRequest, BasketRiskResponse, IndicesResponse } from "../api";
import { priceBasket, stressBasket } from "../api";
import { BasketLegGrid } from "../components/BasketLegGrid";
import { BasketRiskPanel } from "../components/BasketRiskPanel";
import { Metric } from "../components/Metric";
import { StressSurface } from "../components/StressSurface";
import { buildTemplate, TEMPLATE_LABELS, type TemplateName } from "../basketTemplates";
import { useFetch } from "../hooks/useFetch";
import { signedMoney } from "../lib/format";
import type { BasketScenariosResponse } from "../stressApi";

const TEMPLATES: TemplateName[] = ["straddle", "strangle", "risk_reversal"];

// Compose a multi-leg basket and price/risk it off the Tab-1 analytics (WS 2A). The operator
// picks an underlying, trade date and tenor, builds legs (by hand or a one-click template), then
// prices the basket — the panel shows the book-additive dollar Greeks and the per-leg breakdown.
export function BasketPage() {
  // The underlying is chosen from the registry's enabled set (GET /api/indices) — never a
  // hard-coded ticker. A basket can only be priced on a captured index (the chain is captured
  // at the index level), so the picker is constrained to enabled indices.
  const indices = useFetch<IndicesResponse>("/api/indices");
  const indexOptions = useMemo(() => indices.data?.indices ?? [], [indices.data]);
  const [underlying, setUnderlying] = useState("");
  // Land on the first enabled index when the registry list arrives, and keep the selection
  // valid if the enabled set changes (e.g. an index is parked) under it.
  useEffect(() => {
    if (indexOptions.length === 0) return;
    if (!underlying || !indexOptions.some((o) => o.symbol === underlying)) {
      setUnderlying(indexOptions[0].symbol);
    }
  }, [indexOptions, underlying]);
  const [tradeDate, setTradeDate] = useState("");
  const [tenor, setTenor] = useState("1m");
  const [legs, setLegs] = useState<BasketLegInput[]>([]);
  const [result, setResult] = useState<BasketRiskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [stress, setStress] = useState<BasketScenariosResponse | null>(null);
  const [stressError, setStressError] = useState<string | null>(null);
  const [stressLoading, setStressLoading] = useState(false);

  function addLeg(leg: BasketLegInput) {
    setLegs((current) => [...current, leg]);
  }
  function removeLeg(index: number) {
    setLegs((current) => current.filter((_, i) => i !== index));
  }
  function applyTemplate(name: TemplateName) {
    setLegs(buildTemplate(name, underlying, tenor));
  }

  function composedBasket(): BasketRequest {
    return {
      basket_id: `basket-${underlying}-${tradeDate || "latest"}`,
      trade_date: tradeDate,
      underlying,
      legs,
    };
  }

  async function price() {
    setError(null);
    setLoading(true);
    try {
      setResult(await priceBasket(composedBasket()));
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function runStress() {
    setStressError(null);
    setStressLoading(true);
    try {
      setStress(await stressBasket(composedBasket()));
    } catch (err) {
      setStress(null);
      setStressError(err instanceof Error ? err.message : String(err));
    } finally {
      setStressLoading(false);
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
          <select aria-label="underlying" value={underlying}
            disabled={indexOptions.length === 0}
            onChange={(e) => setUnderlying(e.target.value)}>
            {indexOptions.map((item) => (
              <option key={item.symbol} value={item.symbol}>
                {item.name} ({item.symbol})
              </option>
            ))}
          </select>
        </label>
        <label>
          {/* Empty means "latest banked day": the BFF resolves it to the most recent analytics
              partition for the underlying, so the default flow prices without picking a date. */}
          Trade date (empty = latest){" "}
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

      <div className="basket-actions">
        <button type="button" onClick={price} disabled={loading || legs.length === 0}>
          {loading ? "Pricing…" : "Price basket"}
        </button>
        <button type="button" onClick={runStress} disabled={stressLoading || legs.length === 0}>
          {stressLoading ? "Stressing…" : "Stress basket"}
        </button>
      </div>

      {error !== null && (
        <p role="alert" className="error">
          Failed to price basket: {error}
        </p>
      )}
      {result !== null && <BasketRiskPanel result={result} />}

      {stressError !== null && (
        <p role="alert" className="error">
          Failed to stress basket: {stressError}
        </p>
      )}
      {stress !== null && (
        <div className="risk-grid">
          <article className="panel scenario-summary">
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">{stress.underlying}</p>
                <h2>Worst case</h2>
              </div>
              <span className="status negative">
                {stress.n_resolved}/{stress.n_legs} legs repriced
              </span>
            </div>
            <div className="quote-strip">
              <Metric label="Worst PnL" value={signedMoney(stress.worst_case.pnl)} />
              <Metric
                label="Spot shock"
                value={`${(stress.worst_case.spot_shock * 100).toFixed(0)}%`}
              />
              <Metric
                label="Vol shock"
                value={`${(stress.worst_case.vol_shock * 100).toFixed(0)} pts`}
              />
            </div>
            {stress.n_gaps > 0 && (
              <p role="status">
                {stress.n_gaps} leg(s) not repriced:{" "}
                {stress.gaps
                  .map(
                    (gap) =>
                      `${gap.tenor_label ?? gap.underlying}/${gap.delta_band ?? "stock"} (${gap.reason})`,
                  )
                  .join(", ")}
              </p>
            )}
          </article>
          <StressSurface
            surface={stress.surface}
            kicker={`${stress.underlying} ${stress.trade_date}`}
          />
        </div>
      )}
    </section>
  );
}
