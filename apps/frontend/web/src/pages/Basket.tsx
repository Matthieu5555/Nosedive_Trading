import { useEffect, useMemo, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/ui/tabs";

import type {
  AttributionResponse,
  BasketLegInput,
  BasketRequest,
  BasketRiskResponse,
  ComposeLayerInput,
  ComposeRequest,
  ComposeResponse,
  DeltaBandsResponse,
  IndicesResponse,
  SubStrategiesResponse,
} from "../api";
import { composeBook, fetchAttribution, priceBasket, stressBasket } from "../api";
import { buildTemplate, TEMPLATE_LABELS, type TemplateName } from "../basketTemplates";
import { BasketLegGrid } from "../components/BasketLegGrid";
import { TicketPanel } from "../components/TicketPanel";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol } from "../lib/format";
import type { BasketScenariosResponse, ScenariosResponse } from "../stressApi";
import { AttributionTab } from "./basket/AttributionTab";
import { BuildPriceTab } from "./basket/BuildPriceTab";
import { ComposeTab } from "./basket/ComposeTab";
import { LeBookSection } from "./basket/LeBookSection";
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

  // ① Composer — book composition (B1): layer decorrelated sub-strategies into one book.
  const subStrategies = useFetch<SubStrategiesResponse>("/api/compose/sub-strategies");
  const [layers, setLayers] = useState<ComposeLayerInput[]>([]);
  const [book, setBook] = useState<ComposeResponse | null>(null);
  const [composeError, setComposeError] = useState<string | null>(null);
  const [composeLoading, setComposeLoading] = useState(false);

  // ③ Choquer — named historical crises, folded in from the standalone Risk Scenarios page as
  // shock presets. The persisted Risk path serves the named scenarios; the basket reuses them.
  const namedScenarios = useFetch<ScenariosResponse>("/api/risk/scenarios");

  function addLeg(leg: BasketLegInput) {
    setLegs((current) => [...current, leg]);
  }
  function removeLeg(index: number) {
    setLegs((current) => current.filter((_, i) => i !== index));
  }
  function applyTemplate(name: TemplateName) {
    setLegs(buildTemplate(name, underlying, tenor));
  }

  function addLayer(layer: ComposeLayerInput) {
    setLayers((current) => [...current, layer]);
  }
  function removeLayer(index: number) {
    setLayers((current) => current.filter((_, i) => i !== index));
  }
  function moveLayer(index: number, direction: -1 | 1) {
    setLayers((current) => {
      const target = index + direction;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
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

  async function runCompose() {
    setComposeError(null);
    setComposeLoading(true);
    try {
      const body: ComposeRequest = {
        book_id: `book-${underlying}-${tradeDate || "latest"}`,
        trade_date: tradeDate || undefined,
        layers,
      };
      setBook(await composeBook(body));
    } catch (err) {
      setBook(null);
      setComposeError(err instanceof Error ? err.message : String(err));
    } finally {
      setComposeLoading(false);
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
          <p className="eyebrow">Compose a book, then shock it</p>
          <h1>Basket Builder</h1>
        </div>
      </div>
      <p>
        Compose a book — legs and layered sub-strategies — read it, shock it across spot/vol/rate
        and the named crises, then explain its P&amp;L by Greek. The underlying and date below are
        shared across every block.
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

      <Tabs defaultValue="compose" className="market-tabs">
        <div className="market-tabs__bar max-w-full overflow-x-auto">
          <TabsList className="market-tabs__list max-w-none">
            <TabsTrigger value="compose">① Composer</TabsTrigger>
            <TabsTrigger value="book">② Le book</TabsTrigger>
            <TabsTrigger value="stress">③ Choquer</TabsTrigger>
            <TabsTrigger value="attribution">④ Attribution</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="compose">
          <BuildPriceTab
            canPrice={legs.length > 0}
            loading={loading}
            error={error}
            result={result}
            currency={currency}
            onPrice={price}
          />
          <ComposeTab
            subStrategies={subStrategies.data?.sub_strategies ?? []}
            subStrategiesLoading={subStrategies.loading}
            subStrategiesError={subStrategies.error}
            layers={layers}
            bands={deltaBands.data?.delta_bands ?? []}
            loading={composeLoading}
            error={composeError}
            book={book}
            currency={currency}
            tradeDate={tradeDate}
            onAddLayer={addLayer}
            onRemoveLayer={removeLayer}
            onMoveLayer={moveLayer}
            onCompose={runCompose}
          />
        </TabsContent>

        <TabsContent value="book">
          <LeBookSection underlying={underlying} tradeDate={tradeDate} currency={currency} />
        </TabsContent>

        <TabsContent value="stress">
          <StressTab
            canStress={legs.length > 0}
            loading={stressLoading}
            error={stressError}
            stress={stress}
            currency={currency}
            onStress={runStress}
            namedScenarios={namedScenarios.data?.named ?? []}
            namedLoading={namedScenarios.loading}
            namedError={namedScenarios.error}
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
