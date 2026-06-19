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
} from "../../api";
import { composeBook, fetchAttribution, priceBasket, stressBasket } from "../../api";
import { buildTemplate, TEMPLATE_LABELS, type TemplateName } from "../../basketTemplates";
import { BasketLegGrid } from "../../components/BasketLegGrid";
import { Cluster, Stack } from "../../components/layout";
import { TicketPanel } from "../../components/TicketPanel";
import { useFetch } from "../../hooks/useFetch";
import { currencySymbol } from "../../lib/format";
import { tourAnchor } from "../../lib/tour";
import type { BasketScenariosResponse, ScenariosResponse } from "../../stressApi";
import { AttributionTab } from "../basket/AttributionTab";
import { BuildPriceTab } from "../basket/BuildPriceTab";
import { ComposeTab } from "../basket/ComposeTab";
import { StressTab } from "../basket/StressTab";

const TEMPLATES: TemplateName[] = ["straddle", "strangle", "risk_reversal"];

// Simulate / "Build a basket" mode: compose a hypothetical book of option legs on the spot and
// shock it on demand. This is the old Basket Builder, with the "The Book" sub-tab removed (it was a
// verbatim clone of the Positions page); to read the real book, use the Positions tab.
export function BuildBasket() {
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

  // ① Compose — book composition (B1): layer decorrelated sub-strategies into one book.
  const subStrategies = useFetch<SubStrategiesResponse>("/api/compose/sub-strategies");
  const [layers, setLayers] = useState<ComposeLayerInput[]>([]);
  const [book, setBook] = useState<ComposeResponse | null>(null);
  const [composeError, setComposeError] = useState<string | null>(null);
  const [composeLoading, setComposeLoading] = useState(false);

  // ② Stress — named historical crises, served by the persisted Risk path as shock presets so the
  // composed basket replays the same labelled crises the held-book view stresses against.
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
    <Stack gap="md">
      <p>
        Compose a hypothetical book, legs and layered sub-strategies, read it, shock it across
        spot/vol/rate and the named crises, then explain its P&amp;L by Greek. The underlying and
        date below are shared across every block.
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

      <Stack gap="md">
        <article className="panel" aria-label="Compose the basket">
          <Stack gap="md">
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">{underlying || "Basket"}</p>
                <h2>Compose the basket</h2>
              </div>
              <span className="status">shared underlying &amp; date</span>
            </div>

            <Cluster gap="sm" align="end">
              <label>
                Underlying{" "}
                <select
                  aria-label="underlying"
                  {...tourAnchor(
                    "basket.underlying",
                    "Underlying picker",
                    "Choose the underlying index the basket is built on.",
                  )}
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
                <input
                  aria-label="tenor"
                  value={tenor}
                  onChange={(e) => setTenor(e.target.value)}
                />
              </label>
            </Cluster>

            <Cluster
              gap="xs"
              role="group"
              aria-label="templates"
              {...tourAnchor(
                "basket.templates",
                "Strategy templates",
                "One click fills the basket with a ready-made shape like a straddle or strangle.",
              )}
            >
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
            </Cluster>

            <BasketLegGrid
              legs={legs}
              defaultUnderlying={underlying}
              defaultTenor={tenor}
              bands={deltaBands.data?.delta_bands ?? []}
              onAdd={addLeg}
              onRemove={removeLeg}
            />
          </Stack>
        </article>

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
            <TabsList
              className="market-tabs__list max-w-none"
              {...tourAnchor(
                "basket.tabs",
                "Basket workflow tabs",
                "Move between composing the book, stressing it, and explaining its P&L.",
              )}
            >
              {/* Drop the shadcn min-w so each pill hugs its own label; with the list's uniform
                  gap the inter-pill rhythm is then even, instead of short labels (Stress) padding
                  out to the same min-width as long ones and reading as uneven spacing. */}
              <TabsTrigger value="compose" className="min-w-0">
                ① Compose
              </TabsTrigger>
              <TabsTrigger value="stress" className="min-w-0">
                ② Stress
              </TabsTrigger>
              <TabsTrigger value="attribution" className="min-w-0">
                ③ Attribution
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="compose">
            {/* Two sibling panels share this tab; the Stack owns the gap between them so the
                Price and Compose cards never touch, matching every other tab's rhythm. */}
            <Stack gap="md">
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
            </Stack>
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
      </Stack>
    </Stack>
  );
}
