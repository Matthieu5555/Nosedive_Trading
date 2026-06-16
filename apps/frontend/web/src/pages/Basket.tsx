import { useEffect, useMemo, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/ui/tabs";

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
import { BasketLegGrid } from "../components/BasketLegGrid";
import { TicketPanel } from "../components/TicketPanel";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol } from "../lib/format";
import type { BasketScenariosResponse } from "../stressApi";
import { AttributionTab } from "./basket/AttributionTab";
import { BuildPriceTab } from "./basket/BuildPriceTab";
import { StressTab } from "./basket/StressTab";

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
        Compose a multi-leg position once, then move between building &amp; pricing it, stressing
        it, and reading a book's P&amp;L attribution — the legs, underlying and date below are
        shared across all three.
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
          basketId={`basket-${underlying}-${tradeDate || "latest"}`}
          underlying={underlying}
          tradeDate={tradeDate}
          legs={legs}
        />
      )}

      <Tabs defaultValue="build" className="market-tabs">
        <div className="market-tabs__bar">
          <TabsList className="market-tabs__list">
            <TabsTrigger value="build">Build &amp; price</TabsTrigger>
            <TabsTrigger value="stress">Stress</TabsTrigger>
            <TabsTrigger value="attribution">Attribution</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="build">
          <BuildPriceTab
            canPrice={legs.length > 0}
            loading={loading}
            error={error}
            result={result}
            currency={currency}
            onPrice={price}
          />
        </TabsContent>

        <TabsContent value="stress">
          <StressTab
            canStress={legs.length > 0}
            loading={stressLoading}
            error={stressError}
            stress={stress}
            currency={currency}
            onStress={runStress}
          />
        </TabsContent>

        <TabsContent value="attribution">
          <AttributionTab
            portfolioId={portfolioId}
            onPortfolioId={setPortfolioId}
            tradeDate={tradeDate}
            loading={attributionLoading}
            error={attributionError}
            attribution={attribution}
            onLoad={loadAttribution}
          />
        </TabsContent>
      </Tabs>
    </section>
  );
}
