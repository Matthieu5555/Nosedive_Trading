import { useEffect, useMemo, useState } from "react";

import type {
  AttributionResponse,
  BasketLegInput,
  BasketRequest,
  BasketRiskResponse,
  DeltaBandsResponse,
  IndicesResponse,
} from "../api";
import { fetchAttribution, priceBasket, stressBasket } from "../api";
import { buildTemplate, TEMPLATE_LABELS, type TemplateName } from "../basketTemplates";
import { AttributionWaterfall } from "../components/AttributionWaterfall";
import { BasketLegGrid } from "../components/BasketLegGrid";
import { BasketRiskPanel } from "../components/BasketRiskPanel";
import { Metric } from "../components/Metric";
import { StressSurface } from "../components/StressSurface";
import { TicketPanel } from "../components/TicketPanel";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol, sciUnit, UNITS, withCurrency } from "../lib/format";
import type { BasketScenariosResponse } from "../stressApi";

const TEMPLATES: TemplateName[] = ["straddle", "strangle", "risk_reversal"];

export function BasketPage() {
  const indices = useFetch<IndicesResponse>("/api/indices");
  const indexOptions = useMemo(() => indices.data?.indices ?? [], [indices.data]);
  const [underlying, setUnderlying] = useState("");

  useEffect(() => {
    if (indexOptions.length === 0) return;
    if (!underlying || !indexOptions.some((o) => o.symbol === underlying)) {
      setUnderlying(indexOptions[0].symbol);
    }
  }, [indexOptions, underlying]);

  const currency = currencySymbol(indexOptions.find((o) => o.symbol === underlying)?.currency);

  const deltaBands = useFetch<DeltaBandsResponse>("/api/config/delta-bands");
  const [tradeDate, setTradeDate] = useState("");
  const [tenor, setTenor] = useState("1m");
  const [legs, setLegs] = useState<BasketLegInput[]>([]);
  const [result, setResult] = useState<BasketRiskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [stress, setStress] = useState<BasketScenariosResponse | null>(null);
  const [stressError, setStressError] = useState<string | null>(null);
  const [stressLoading, setStressLoading] = useState(false);

  const [portfolioId, setPortfolioId] = useState("");
  const [attribution, setAttribution] = useState<AttributionResponse | null>(null);
  const [attributionError, setAttributionError] = useState<string | null>(null);
  const [attributionLoading, setAttributionLoading] = useState(false);

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

  async function loadAttribution() {
    setAttributionError(null);
    setAttributionLoading(true);
    try {
      setAttribution(
        await fetchAttribution({
          tradeDate: tradeDate || undefined,
          portfolioId: portfolioId || undefined,
          level: "book",
        }),
      );
    } catch (err) {
      setAttribution(null);
      setAttributionError(err instanceof Error ? err.message : String(err));
    } finally {
      setAttributionLoading(false);
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

      {/* The registry-driven inputs (the underlying list and the delta-band axis) are fetched up
          front; a failure used to only disable the dropdown / empty the leg grid with no word why.
          Surface it so the operator knows the controls are degraded because a fetch failed. */}
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
          {/* Empty means "latest banked day": the BFF resolves it to the most recent analytics
              partition for the underlying, so the default flow prices without picking a date. */}
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
        <label>
          {/* The portfolio whose persisted P&L attribution to drill into (book level). */}
          Portfolio (attribution){" "}
          <input
            aria-label="portfolio"
            value={portfolioId}
            onChange={(e) => setPortfolioId(e.target.value)}
          />
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

      <div className="basket-actions">
        <button type="button" onClick={price} disabled={loading || legs.length === 0}>
          {loading ? "Pricing…" : "Price basket"}
        </button>
        <button type="button" onClick={runStress} disabled={stressLoading || legs.length === 0}>
          {stressLoading ? "Stressing…" : "Stress basket"}
        </button>
        <button type="button" onClick={loadAttribution} disabled={attributionLoading}>
          {attributionLoading ? "Loading attribution…" : "P&L attribution"}
        </button>
      </div>

      {legs.length > 0 && (
        <TicketPanel
          basketId={`basket-${underlying}-${tradeDate || "latest"}`}
          underlying={underlying}
          tradeDate={tradeDate}
          legs={legs}
        />
      )}

      {error !== null && (
        <p role="alert" className="error">
          Failed to price basket: {error}
        </p>
      )}
      {result !== null && <BasketRiskPanel result={result} currency={currency} />}

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
              <Metric
                label="Worst PnL"
                value={sciUnit(
                  stress.worst_case.pnl,
                  withCurrency(stress.worst_case.unit, currency),
                )}
              />
              <Metric
                label="Spot shock"
                value={sciUnit(stress.worst_case.spot_shock, UNITS.shock)}
              />
              <Metric label="Vol shock" value={sciUnit(stress.worst_case.vol_shock, UNITS.shock)} />
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
            currency={currency}
          />
        </div>
      )}

      {attributionError !== null && (
        <p role="alert" className="error">
          Failed to load attribution: {attributionError}
        </p>
      )}
      {attribution !== null && (
        <div className="risk-grid">
          <AttributionWaterfall
            attribution={attribution}
            kicker={`${portfolioId || "portfolio"} ${tradeDate || "latest"}`}
          />
        </div>
      )}
    </section>
  );
}
