import { useEffect, useMemo, useState } from "react";

import {
  ALL_MATURITIES,
  type AnalyticsMaturity,
  type AnalyticsResponse,
  type ConstituentsResponse,
  type IndicesResponse,
  type PriceHistoryResponse,
  type RecordedDatesResponse,
  type SignalsResponse,
  type SurfaceDense,
  type SurfaceSide,
} from "../api";
import { EMPTY_FRAME, useSetAssistantFrame } from "../components/Assistant/AssistantContext";
import { AsyncBlock } from "../components/AsyncBlock";
import { describeSurface, type SurfaceCoverage, type SurfaceMode } from "../components/charts";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Cluster, Stack } from "../components/layout";
import { CoveragePanelBlock } from "../components/market/CoveragePanelBlock";
import { DispersionPanel } from "../components/market/DispersionPanel";
import { PricePanel } from "../components/market/PricePanel";
import { ScorecardsPanel } from "../components/market/ScorecardsPanel";
import { type MaturityFloorOption, SurfacePanel } from "../components/market/SurfacePanel";
import { TenorWorkspace } from "../components/market/TenorWorkspace";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol } from "../lib/format";
import { ConstituentsWorkspace } from "./market/ConstituentsWorkspace";
import { AsOfSelect, QcBadge } from "./market/marketHeader";
import { useMarketTicker } from "./market/useMarketTicker";

export function MarketPage() {
  const indices = useFetch<IndicesResponse>("/api/indices");
  const indexOptions = useMemo(() => indices.data?.indices ?? [], [indices.data]);

  const [index, setIndex] = useState("");
  useEffect(() => {
    if (indexOptions.length === 0) return;
    if (!index || !indexOptions.some((o) => o.symbol === index)) {
      setIndex(indexOptions[0].symbol);
    }
  }, [indexOptions, index]);

  // The page-driving selected ticker: the index/ETF itself or one of its constituents. It is the
  // underlying every analytics panel below re-renders for. Page-scoped (see useMarketTicker); changing
  // the index re-lands it on the index, so a stale member never drives the wrong index's panels.
  const ticker = useMarketTicker(index);
  const subject = ticker.ticker;

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  // Strict is the landing state every time (canonical-first, MAT-LEGIBILITY-strict-indicative-mode):
  // switching the ticker re-lands on strict so indicative is always a deliberate act, never a sticky
  // state that could silently outlive the surface it was toggled on.
  const [surfaceMode, setSurfaceMode] = useState<SurfaceMode>("strict");
  useEffect(() => {
    setSurfaceMode("strict");
  }, [subject]);

  // Clean surface is the landing state: it draws the smooth, fully FILLED nappe. Turning it off shows
  // the RAW, less-interpolated surface that keeps the holes where strikes stop. Resets to clean
  // whenever the ticker changes, so "raw" is always deliberate.
  const [cleanSurface, setCleanSurface] = useState(true);
  useEffect(() => {
    setCleanSurface(true);
  }, [subject]);

  // Call / Put / Combined is a first-class selector; Combined is the landing read. The Maturity
  // control is a FLOOR, not a single point. Both re-land when the ticker changes so a per-side or
  // floored view is always a deliberate choice for the new ticker.
  const [surfaceSide, setSurfaceSide] = useState<SurfaceSide>("combined");
  const [maturityFloorYears, setMaturityFloorYears] = useState<number>(0);
  useEffect(() => {
    setSurfaceSide("combined");
    setMaturityFloorYears(0);
  }, [subject]);

  // Recorded fetches + constituents are keyed off the INDEX (the ETF defines the capture runs and the
  // membership), not the active ticker. The analytics/price/signals panels key off the active ticker.
  const recorded = useFetch<RecordedDatesResponse>(
    index ? `/api/recorded-dates?index=${encodeURIComponent(index)}` : "",
  );
  const constituents = useFetch<ConstituentsResponse>(
    index ? `/api/constituents?index=${encodeURIComponent(index)}` : "",
  );

  const available = recorded.data?.available ?? [];
  const selectedFetch =
    available.find((fetch) => (fetch.run_id ?? fetch.date) === selectedKey) ?? available[0] ?? null;
  const effectiveRunId = selectedFetch?.run_id ?? null;
  const effectiveAsOf = selectedFetch?.date ?? null;
  const selectedFetchKey = selectedFetch ? (selectedFetch.run_id ?? selectedFetch.date) : null;

  const analytics = useFetch<AnalyticsResponse>(
    subject && effectiveAsOf
      ? `/api/analytics?underlying=${encodeURIComponent(subject)}&trade_date=${encodeURIComponent(effectiveAsOf)}` +
          `&mode=${encodeURIComponent(surfaceMode)}` +
          (effectiveRunId ? `&run_id=${encodeURIComponent(effectiveRunId)}` : "")
      : "",
  );
  const price = useFetch<PriceHistoryResponse>(
    subject && effectiveAsOf
      ? `/api/price-history?underlying=${encodeURIComponent(subject)}&end=${encodeURIComponent(effectiveAsOf)}`
      : "",
  );
  const signals = useFetch<SignalsResponse>(
    subject && effectiveAsOf
      ? `/api/signals?underlying=${encodeURIComponent(subject)}&trade_date=${encodeURIComponent(effectiveAsOf)}`
      : "",
  );
  // Dispersion is an INDEX property (correlation across members), so it always reads the index's
  // signal, never the active constituent's. When the index IS the active ticker, the ticker signals
  // already carry it; only a constituent ticker needs a separate index-scoped fetch.
  const indexSignals = useFetch<SignalsResponse>(
    ticker.kind === "constituent" && index && effectiveAsOf
      ? `/api/signals?underlying=${encodeURIComponent(index)}&trade_date=${encodeURIComponent(effectiveAsOf)}`
      : "",
  );
  const dispersion = ticker.kind === "constituent" ? indexSignals : signals;

  // Which sides the close actually captured. A side with no maturities is offered disabled in the
  // selector (the honest "not captured" state), never silently swapped for combined.
  const sidesAvailable = useMemo<SurfaceSide[]>(
    () => analytics.data?.sides_available ?? (analytics.data ? ["combined"] : []),
    [analytics.data],
  );
  const perSideServed = analytics.data?.sides_available !== undefined;
  // The per-side captured maturities (combined / call / put). Passed straight to the Dollar Greeks
  // table and the Price-structure order book so their own Combined / Calls / Puts toggles read the
  // real per-side capture, the same dimension the surface reads. Absent on an older payload, in which
  // case the tables fall back to the combined `maturities` and disable Calls / Puts.
  const sides = analytics.data?.sides;
  const effectiveSide: SurfaceSide = sidesAvailable.includes(surfaceSide)
    ? surfaceSide
    : "combined";
  const perSideFitMissing = surfaceSide !== "combined" && !sidesAvailable.includes(surfaceSide);

  const sideMaturities = useMemo<AnalyticsMaturity[]>(() => {
    const sides = analytics.data?.sides;
    if (sides && effectiveSide in sides) return sides[effectiveSide];
    return analytics.data?.maturities ?? [];
  }, [analytics.data, effectiveSide]);
  const sideSurface = useMemo<SurfaceDense | null>(() => {
    const bySide = analytics.data?.surfaces_by_side;
    if (bySide && effectiveSide in bySide) return bySide[effectiveSide];
    return analytics.data?.surface ?? null;
  }, [analytics.data, effectiveSide]);

  const surfaceSideMaturities = sideMaturities;

  const maturityFloorOptions = useMemo<MaturityFloorOption[]>(() => {
    const sorted = [...surfaceSideMaturities].sort((a, b) => a.maturity_years - b.maturity_years);
    const floors = sorted
      .slice(0, Math.max(sorted.length - 1, 0))
      .map((m) => ({ years: m.maturity_years, label: `min ${m.tenor_label || m.label} and up` }));
    return [{ years: 0, label: ALL_MATURITIES }, ...floors];
  }, [surfaceSideMaturities]);
  const effectiveFloorYears = maturityFloorOptions.some((o) => o.years === maturityFloorYears)
    ? maturityFloorYears
    : 0;
  const surfaceMaturities = useMemo(
    () => surfaceSideMaturities.filter((m) => m.maturity_years >= effectiveFloorYears),
    [surfaceSideMaturities, effectiveFloorYears],
  );

  // The index quote-currency ISO code, resolved once from the indices payload. (The currency follows
  // the index, not the active ticker: constituents are quoted in the index's currency here.)
  const currencyCode = indexOptions.find((o) => o.symbol === index)?.currency ?? null;
  const currency = currencySymbol(currencyCode);

  // Feed the globally-mounted floating assistant the live Market frame: the active ticker, close and
  // mode the charts are showing. On unmount we clear it back to empty.
  const setAssistantFrame = useSetAssistantFrame();
  useEffect(() => {
    setAssistantFrame({
      underlying: subject,
      asOf: effectiveAsOf,
      runId: effectiveRunId,
      mode: surfaceMode,
      focusedElementId: null,
    });
    return () => setAssistantFrame(EMPTY_FRAME);
  }, [setAssistantFrame, subject, effectiveAsOf, effectiveRunId, surfaceMode]);

  return (
    <Stack as="section" className="page" gap="md">
      <div className="page-header">
        <div>
          <p className="eyebrow">Form a view on what the market is pricing</p>
          <h1>Market</h1>
        </div>
        <Cluster className="control-row" gap="sm" align="end">
          <AsOfSelect
            recorded={recorded.data}
            value={selectedFetchKey}
            onChange={(key) => setSelectedKey(key)}
          />
        </Cluster>
      </div>

      <AsyncBlock
        loading={indices.loading || recorded.loading}
        error={indices.error ?? recorded.error}
      >
        {recorded.data &&
          (() => {
            if (available.length === 0 || effectiveAsOf === null) {
              return (
                <article className="panel">
                  <p>No capture runs to show for {recorded.data.index} yet.</p>
                </article>
              );
            }
            const qc = selectedFetch?.qc ?? "unknown";
            const instant = analytics.data?.close_instant ?? null;
            const surfaceMissing =
              analytics.data !== null && analytics.data.maturities.length === 0;
            // The mode and coverage that drive BOTH the descriptor sentence and the chart captions are
            // resolved once here and threaded down, so the panel heading, the caption and the figure
            // caption can never disagree.
            const block = analytics.data?.coverage ?? null;
            const surfaceCoverage: SurfaceCoverage | null = block
              ? surfaceMode === "indicative"
                ? {
                    resting: block.option_rows,
                    total: block.option_rows,
                    indicative: block.excluded,
                  }
                : { resting: block.two_sided, total: block.option_rows }
              : null;
            const descriptor = describeSurface({
              subject,
              asOf: effectiveAsOf,
              closeInstant: instant,
              mode: surfaceMode,
              coverage: surfaceCoverage,
              degenerate: surfaceMissing,
            });
            return (
              <Stack gap="md">
                <Cluster className="market-scroll__status" gap="sm" align="center">
                  <span className="market-scroll__index">{subject}</span>
                  {ticker.kind === "constituent" && (
                    <span className="status">a {index} member</span>
                  )}
                  <span className="status">{descriptor.asOfPhrase}</span>
                  <QcBadge qc={qc} />
                </Cluster>

                <ScorecardsPanel
                  maturities={analytics.data?.maturities ?? null}
                  ivVsRealized={signals.data?.by_kind?.iv_vs_realized?.[0] ?? null}
                  termStructureSlope={signals.data?.by_kind?.term_structure_slope?.[0] ?? null}
                  ivRank={signals.data?.by_kind?.iv_rank?.[0] ?? null}
                  impliedCorrelation={dispersion.data?.by_kind?.implied_correlation?.[0] ?? null}
                  subject={subject}
                  closeInstant={instant}
                  asOf={effectiveAsOf}
                  runId={effectiveRunId}
                  loading={analytics.loading || signals.loading}
                  error={analytics.error}
                />

                <PricePanel
                  subject={subject}
                  asOfPhrase={descriptor.asOfPhrase}
                  data={price.data}
                  loading={price.loading}
                  error={price.error}
                />

                <ErrorBoundary label="Constituents">
                  <ConstituentsWorkspace
                    asOf={effectiveAsOf}
                    currency={currencyCode}
                    indexSymbol={index}
                    indexName={indexOptions.find((o) => o.symbol === index)?.name ?? null}
                    constituents={constituents.data}
                    loading={constituents.loading}
                    error={constituents.error}
                    activeTicker={subject}
                    onSelectIndex={ticker.selectIndex}
                    onSelectConstituent={ticker.selectConstituent}
                  />
                </ErrorBoundary>

                <SurfacePanel
                  descriptor={descriptor}
                  surfaceMode={surfaceMode}
                  surfaceSide={surfaceSide}
                  sidesAvailable={sidesAvailable}
                  perSideServed={perSideServed}
                  perSideFitMissing={perSideFitMissing}
                  hasData={analytics.data !== null}
                  surfaceMissing={surfaceMissing}
                  maturityFloorYears={effectiveFloorYears}
                  maturityFloorOptions={maturityFloorOptions}
                  cleanSurface={cleanSurface}
                  surfaceSideMaturities={surfaceSideMaturities}
                  surfaceMaturities={surfaceMaturities}
                  sideSurface={sideSurface}
                  subject={subject}
                  asOf={effectiveAsOf}
                  closeInstant={instant}
                  coverage={surfaceCoverage}
                  loading={analytics.loading}
                  error={analytics.error}
                  onSideChange={setSurfaceSide}
                  onFloorChange={setMaturityFloorYears}
                  onCleanChange={setCleanSurface}
                  onModeChange={setSurfaceMode}
                />

                <ErrorBoundary label="Tenor workspace">
                  <AsyncBlock
                    loading={analytics.loading}
                    error={analytics.error}
                    height={360}
                    subject={`the ${subject} smile, Greeks, price book and rates`}
                  >
                    {analytics.data && (
                      <TenorWorkspace
                        maturities={surfaceMaturities}
                        currency={currency}
                        subject={subject}
                        asOf={effectiveAsOf}
                        closeInstant={instant}
                        mode={surfaceMode}
                        coverage={surfaceCoverage}
                        side={effectiveSide}
                        sides={sides}
                        sidesAvailable={sidesAvailable}
                        perSideServed={perSideServed}
                      />
                    )}
                  </AsyncBlock>
                </ErrorBoundary>

                <DispersionPanel
                  index={index}
                  asOfPhrase={descriptor.asOfPhrase}
                  signal={dispersion.data?.by_kind?.implied_correlation?.[0] ?? null}
                  loading={dispersion.loading}
                  error={dispersion.error}
                />

                <CoveragePanelBlock
                  underlying={subject}
                  tradeDate={effectiveAsOf}
                  runId={effectiveRunId ?? undefined}
                />
              </Stack>
            );
          })()}
      </AsyncBlock>
    </Stack>
  );
}
