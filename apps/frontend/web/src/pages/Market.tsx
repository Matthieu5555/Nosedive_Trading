import { useEffect, useMemo, useState } from "react";

import {
  ALL_MATURITIES,
  type AnalyticsMaturity,
  type AnalyticsResponse,
  type IndicesResponse,
  type PriceHistoryResponse,
  type RecordedDatesResponse,
  type SignalsResponse,
  type SurfaceDense,
  type SurfaceSide,
  SURFACE_SIDE_LABELS,
} from "../api";
import { EMPTY_FRAME, useSetAssistantFrame } from "../components/Assistant/AssistantContext";
import { AsyncBlock } from "../components/AsyncBlock";
import {
  describeSurface,
  PriceChart,
  type SurfaceCoverage,
  type SurfaceMode,
  VolSurface,
} from "../components/charts";
import { CoveragePanel } from "../components/CoverageTable";
import { DispersionStrip } from "../components/DispersionStrip";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { InfoDot } from "../components/InfoDot";
import { Cluster, Scroll, Stack } from "../components/layout";
import { Scorecards } from "../components/Scorecards";
import { TenorPanel } from "../components/TenorPanel";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol } from "../lib/format";
import { cleanSurfaceMaturities } from "../lib/volRobust";
import { ConstituentsWorkspace } from "./market/ConstituentsWorkspace";
import { AsOfSelect, QcBadge } from "./market/marketHeader";

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

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [selectedMember, setSelectedMember] = useState<string | null>(null);
  const [coverageOpen, setCoverageOpen] = useState(false);
  // Strict is the landing state every time (canonical-first, MAT-LEGIBILITY-strict-indicative-mode):
  // switching the underlying re-lands on strict so indicative is always a deliberate act, never a
  // sticky state that could silently outlive the surface it was toggled on.
  const [surfaceMode, setSurfaceMode] = useState<SurfaceMode>("strict");
  useEffect(() => {
    setSurfaceMode("strict");
  }, [index]);

  // Clean surface is the landing state: the front-week / degenerate slices the fitter could not fit
  // (and which draw the impossible delta spike) are dropped from the smile, Greeks and 3D surface.
  // Turning it off shows every slice raw, spike and all, for the rare case the PM wants to inspect a
  // flagged fit. Resets to clean whenever the underlying changes, so "show all" is always deliberate.
  const [cleanSurface, setCleanSurface] = useState(true);
  useEffect(() => {
    setCleanSurface(true);
  }, [index]);

  // Call / Put / Combined is a first-class selector now (the owner ask: calls and puts have
  // different skew, so each is its own surface). Combined is the landing read; switching the
  // underlying re-lands on it so a per-side view is always a deliberate choice. The Maturity
  // control is a FLOOR, not a single point: it keeps every captured tenor at or above the chosen
  // lower bound, so the 3D surface always renders (a surface needs several tenors). The value is a
  // maturity in years; 0 (ALL_MATURITIES) is "no floor". A single-tenor 2D smile already lives in
  // the Smile & Greeks panel below, so the surface control never collapses the surface to a slice.
  const [surfaceSide, setSurfaceSide] = useState<SurfaceSide>("combined");
  const [maturityFloorYears, setMaturityFloorYears] = useState<number>(0);
  useEffect(() => {
    setSurfaceSide("combined");
    setMaturityFloorYears(0);
  }, [index]);

  const recorded = useFetch<RecordedDatesResponse>(
    index ? `/api/recorded-dates?index=${encodeURIComponent(index)}` : "",
  );

  const available = recorded.data?.available ?? [];
  const selectedFetch =
    available.find((fetch) => (fetch.run_id ?? fetch.date) === selectedKey) ?? available[0] ?? null;
  const effectiveRunId = selectedFetch?.run_id ?? null;
  const effectiveAsOf = selectedFetch?.date ?? null;
  const selectedFetchKey = selectedFetch ? (selectedFetch.run_id ?? selectedFetch.date) : null;

  const analytics = useFetch<AnalyticsResponse>(
    index && effectiveAsOf
      ? `/api/analytics?underlying=${encodeURIComponent(index)}&trade_date=${encodeURIComponent(effectiveAsOf)}` +
          `&mode=${encodeURIComponent(surfaceMode)}` +
          (effectiveRunId ? `&run_id=${encodeURIComponent(effectiveRunId)}` : "")
      : "",
  );
  const price = useFetch<PriceHistoryResponse>(
    index && effectiveAsOf
      ? `/api/price-history?underlying=${encodeURIComponent(index)}&end=${encodeURIComponent(effectiveAsOf)}`
      : "",
  );
  const signals = useFetch<SignalsResponse>(
    index && effectiveAsOf
      ? `/api/signals?underlying=${encodeURIComponent(index)}&trade_date=${encodeURIComponent(effectiveAsOf)}`
      : "",
  );

  // Which sides the close actually captured. A side with no maturities is offered disabled in the
  // selector (the honest "not captured" state), never silently swapped for combined.
  const sidesAvailable = useMemo<SurfaceSide[]>(
    () => analytics.data?.sides_available ?? (analytics.data ? ["combined"] : []),
    [analytics.data],
  );
  // Whether THIS payload carries the per-side block at all. When it doesn't (an older BFF build that
  // predates the per-call/per-put surfaces), Calls/Puts are disabled because the backend serving the
  // page can't supply them yet, NOT because the close failed to capture them. The disabled tooltip
  // and an inline note say which, so a greyed-out Calls/Puts reads as "this backend can't serve it"
  // (restart the BFF), never a silent dead control.
  const perSideServed = analytics.data?.sides_available !== undefined;
  // If the chosen side isn't available for this close, fall back to combined (and surface that the
  // per-side fit isn't there) rather than rendering a blank.
  const effectiveSide: SurfaceSide = sidesAvailable.includes(surfaceSide)
    ? surfaceSide
    : "combined";
  const perSideFitMissing = surfaceSide !== "combined" && !sidesAvailable.includes(surfaceSide);

  // The selected side's maturities + dense grid. Falls back to the top-level combined view on an
  // older payload that predates the per-side block, so the page never breaks on a stale BFF.
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

  // The surface/Greeks data path: clean (default) drops the flagged slices; show-all passes raw. Only
  // the smile, Greeks and 3D surface read this; the indicator scorecards stay on the raw (combined)
  // maturities. The maturities now follow the selected side.
  const rawMaturities = sideMaturities;
  const cleanedMaturities = useMemo(
    () => (cleanSurface ? cleanSurfaceMaturities(rawMaturities) : rawMaturities),
    [cleanSurface, rawMaturities],
  );
  const nDroppedSlices = rawMaturities.length - cleanSurfaceMaturities(rawMaturities).length;

  // The maturity FLOOR options: "All maturities" (no floor) plus a "min {tenor} and up" floor for
  // each captured tenor except the last (a floor at the longest tenor would leave a single slice,
  // which is a smile, not a surface). Each option carries the tenor's own maturity-in-years as its
  // threshold, near -> far. Listing the side's own captured tenors keeps the control honest when a
  // side captured a different set than combined.
  const maturityFloorOptions = useMemo<MaturityFloorOption[]>(() => {
    const sorted = [...cleanedMaturities].sort((a, b) => a.maturity_years - b.maturity_years);
    const floors = sorted
      .slice(0, Math.max(sorted.length - 1, 0))
      .map((m) => ({ years: m.maturity_years, label: `min ${m.tenor_label || m.label} and up` }));
    return [{ years: 0, label: ALL_MATURITIES }, ...floors];
  }, [cleanedMaturities]);
  // Keep the floor valid as the side / clean toggle changes the captured set (a floor the new set
  // no longer offers falls back to "all maturities" rather than silently emptying the surface).
  const effectiveFloorYears = maturityFloorOptions.some((o) => o.years === maturityFloorYears)
    ? maturityFloorYears
    : 0;
  // The surface always keeps every tenor at or above the floor, so a real 3D surface always renders.
  // (The Smile & Greeks panel below reads this filtered set; the 3D surface slices the dense grid
  // itself, see below.)
  const surfaceMaturities = useMemo(
    () => cleanedMaturities.filter((m) => m.maturity_years >= effectiveFloorYears),
    [cleanedMaturities, effectiveFloorYears],
  );

  // The index quote-currency ISO code (EUR/USD/...), resolved once from the indices payload. The
  // analytics panels render the symbol (currencySymbol); the constituents table takes the ISO code
  // and localises it itself, so latest close reads "€1,624.00" not a bare "1,624.00".
  const currencyCode = indexOptions.find((o) => o.symbol === index)?.currency ?? null;
  const currency = currencySymbol(currencyCode);

  // Feed the globally-mounted floating assistant the live Market frame, so while the user is on this
  // page the assistant grounds on exactly the subject, close and mode the charts are showing. On
  // unmount we clear it back to empty, so navigating away leaves the assistant in its honest "Choose
  // an index and a close" state rather than a stale frame.
  const setAssistantFrame = useSetAssistantFrame();
  useEffect(() => {
    setAssistantFrame({
      underlying: index,
      asOf: effectiveAsOf,
      runId: effectiveRunId,
      mode: surfaceMode,
      focusedElementId: null,
    });
    return () => setAssistantFrame(EMPTY_FRAME);
  }, [setAssistantFrame, index, effectiveAsOf, effectiveRunId, surfaceMode]);

  return (
    <Stack as="section" className="page" gap="md">
      <div className="page-header">
        <div>
          <p className="eyebrow">Form a view on what the market is pricing</p>
          <h1>Market</h1>
        </div>
        <Cluster className="control-row" gap="sm" align="end">
          <select
            aria-label="Index"
            data-tour-id="market.index-picker"
            value={index}
            disabled={indexOptions.length === 0}
            data-hint={index === "" ? "choose-index" : undefined}
            onChange={(event) => {
              setIndex(event.target.value);
              setSelectedKey(null);
              setSelectedMember(null);
            }}
          >
            {indexOptions.map((item) => (
              <option key={item.symbol} value={item.symbol}>
                {item.name} ({item.symbol})
              </option>
            ))}
          </select>
          {index === "" && (
            <span className="status" role="status" data-hint-for="choose-index">
              Choose an index to begin
            </span>
          )}
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
            // The close instant is resolved server-side from the index registry (the BFF's
            // /api/analytics close_instant, derived from configs/universe.yaml calendar +
            // option_settlement_close) — never a hard-coded "17:30 CET" map on the front. Absent →
            // a date-only as-of, never a guessed instant.
            const instant = analytics.data?.close_instant ?? null;
            const surfaceMissing =
              analytics.data !== null && analytics.data.maturities.length === 0;
            // The mode and coverage that drive BOTH the descriptor sentence and the chart captions
            // are resolved once here and threaded down, so the panel heading, the caption and the
            // figure caption can never disagree. Coverage is the one shared block on the payload
            // (option_rows / two_sided / excluded), mapped to the SurfaceCoverage the charts consume;
            // in indicative mode the resting count rises to the captured chain and the lift over the
            // strict two-sided count is the indicative-mark tally. Absent block → null (the caption
            // degrades to "couverture indisponible", never a fabricated fraction).
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
              subject: index,
              asOf: effectiveAsOf,
              closeInstant: instant,
              mode: surfaceMode,
              coverage: surfaceCoverage,
              degenerate: surfaceMissing,
            });
            return (
              <Stack gap="md">
                <Cluster className="market-scroll__status" gap="sm" align="center">
                  <span className="market-scroll__index">{index}</span>
                  <span className="status">{descriptor.asOfPhrase}</span>
                  <QcBadge qc={qc} />
                </Cluster>

                <ErrorBoundary label="Scorecards">
                  <AsyncBlock
                    loading={analytics.loading || signals.loading}
                    error={analytics.error}
                    height={140}
                    subject={`the ${index} indicators`}
                  >
                    {analytics.data && (
                      <Scorecards
                        maturities={analytics.data.maturities}
                        ivVsRealized={signals.data?.by_kind?.iv_vs_realized?.[0] ?? null}
                        termStructureSlope={
                          signals.data?.by_kind?.term_structure_slope?.[0] ?? null
                        }
                        ivRank={signals.data?.by_kind?.iv_rank?.[0] ?? null}
                        impliedCorrelation={signals.data?.by_kind?.implied_correlation?.[0] ?? null}
                        underlying={index}
                        closeInstant={instant}
                        asOf={effectiveAsOf}
                        runId={effectiveRunId}
                      />
                    )}
                  </AsyncBlock>
                </ErrorBoundary>

                <article
                  className="panel"
                  aria-label={`${index} daily history`}
                  data-tour-id="market.price"
                >
                  <Stack gap="md">
                    <div className="panel-heading">
                      <h2>Daily price, {index}</h2>
                      <span className="status">{descriptor.asOfPhrase} · OHLC</span>
                    </div>
                    <ErrorBoundary label="Price">
                      <AsyncBlock
                        loading={price.loading}
                        error={price.error}
                        height={440}
                        subject={`the ${index} price`}
                      >
                        {price.data && (
                          <Scroll label={`${index} daily price chart`}>
                            <PriceChart data={price.data} />
                          </Scroll>
                        )}
                      </AsyncBlock>
                    </ErrorBoundary>
                  </Stack>
                </article>

                <ErrorBoundary label="Constituents">
                  <ConstituentsWorkspace
                    index={index}
                    asOf={effectiveAsOf}
                    currency={currencyCode}
                    selected={selectedMember}
                    onSelect={setSelectedMember}
                  />
                </ErrorBoundary>

                <article
                  className="panel"
                  aria-label={descriptor.subjectHeading}
                  data-tour-id="market.surface"
                >
                  <Stack gap="md">
                    <div className="panel-heading">
                      <div>
                        <h2>
                          {descriptor.subjectHeading}
                          {surfaceMode === "indicative" && (
                            <span
                              className="qc-badge qc-badge--indicative"
                              role="status"
                              aria-label="Indicative mode, not the stored close"
                            >
                              INDICATIVE, not the stored close
                            </span>
                          )}
                        </h2>
                      </div>
                      <div className="panel-heading__controls">
                        <Cluster className="panel-heading__status" gap="xs" align="center">
                          <span className="status" data-tone={descriptor.tone}>
                            {descriptor.caption}
                          </span>
                          <SurfaceFitPill maturities={rawMaturities} />
                        </Cluster>
                        <Cluster className="panel-heading__toggles" gap="xs" align="center">
                          <SurfaceSideToggle
                            side={surfaceSide}
                            available={sidesAvailable}
                            perSideServed={perSideServed}
                            onChange={setSurfaceSide}
                          />
                          <MaturityFloorSelect
                            value={effectiveFloorYears}
                            options={maturityFloorOptions}
                            onChange={setMaturityFloorYears}
                          />
                          <CleanSurfaceToggle
                            clean={cleanSurface}
                            nDropped={nDroppedSlices}
                            onChange={setCleanSurface}
                          />
                          <SurfaceModeToggle mode={surfaceMode} onChange={setSurfaceMode} />
                        </Cluster>
                      </div>
                    </div>
                    {perSideFitMissing && (
                      <p className="state-panel state-panel--note" role="status">
                        Per-side fit not available for this close, showing combined.
                      </p>
                    )}
                    {analytics.data && !perSideServed && (
                      <p className="state-panel state-panel--note" role="status">
                        Calls / Puts are off because the backend serving this page does not yet
                        return the per-side surfaces. Restart the BFF to enable them.
                      </p>
                    )}
                    <ErrorBoundary label="3D surface">
                      <AsyncBlock
                        loading={analytics.loading}
                        error={analytics.error}
                        height={480}
                        subject={`the ${index} surface`}
                      >
                        {analytics.data &&
                          (surfaceMissing ? (
                            <p className="state-panel" role="status">
                              {descriptor.emptyCopy}
                            </p>
                          ) : (
                            // The 3D surface always renders: the maturity control is a floor, not a
                            // single point, so the surface keeps every tenor at or above it (a real
                            // surface needs several tenors). The single-tenor 2D smile lives in the
                            // Smile & Greeks panel below. The dense (fitted-SVI) grid is ALWAYS passed
                            // through, with the floor as a value: VolSurface slices the dense grid to
                            // the rows at or above the floor (clamping so ≥2 tenors always remain), so
                            // a floor trims the short end of the real surface instead of blanking it
                            // (BUG #3). The coarse-cell fallback (surfaceMaturities) stays the genuine
                            // no-dense-surface path.
                            <VolSurface
                              surface={sideSurface}
                              floorYears={effectiveFloorYears}
                              maturities={surfaceMaturities}
                              subject={index}
                              asOf={effectiveAsOf}
                              closeInstant={instant}
                              mode={surfaceMode}
                              coverage={surfaceCoverage}
                            />
                          ))}
                      </AsyncBlock>
                    </ErrorBoundary>
                  </Stack>
                </article>

                <ErrorBoundary label="Tenor view">
                  <AsyncBlock
                    loading={analytics.loading}
                    error={analytics.error}
                    height={360}
                    subject={`the ${index} smile and Greeks`}
                  >
                    {analytics.data && (
                      <TenorPanel
                        maturities={surfaceMaturities}
                        currency={currency}
                        subject={index}
                        asOf={effectiveAsOf}
                        closeInstant={instant}
                        mode={surfaceMode}
                        coverage={surfaceCoverage}
                        side={effectiveSide}
                      />
                    )}
                  </AsyncBlock>
                </ErrorBoundary>

                <article className="panel" aria-label="Dispersion" data-tour-id="market.dispersion">
                  <Stack gap="md">
                    <div className="panel-heading">
                      <Cluster gap="2xs" align="center">
                        <h2>Avg correlation (ρ), {index}</h2>
                        <InfoDot
                          label="Dispersion, how to read it"
                          body={`How tightly the ${index} members are expected to move together. A high average correlation (ρ near 1) means the index moves as one block, so index vol is dear relative to its members; a low ρ means the members move independently, the case for a dispersion trade. Today a realized-vol diagnostic until constituent implied vols land.`}
                        />
                      </Cluster>
                      <span className="status">
                        {descriptor.asOfPhrase} · realized-vol diagnostic
                      </span>
                    </div>
                    <ErrorBoundary label="Dispersion">
                      <AsyncBlock loading={signals.loading} error={signals.error}>
                        {signals.data && (
                          <DispersionStrip
                            index={index}
                            signal={signals.data.by_kind?.implied_correlation?.[0] ?? null}
                          />
                        )}
                      </AsyncBlock>
                    </ErrorBoundary>
                  </Stack>
                </article>

                <article
                  className="panel"
                  aria-label="Capture coverage"
                  data-tour-id="market.coverage"
                >
                  <Stack gap="md">
                    <div className="panel-heading">
                      <h2>Capture coverage</h2>
                      <button
                        type="button"
                        aria-expanded={coverageOpen}
                        onClick={() => setCoverageOpen((open) => !open)}
                      >
                        {coverageOpen ? "Hide" : "Show"}
                      </button>
                    </div>
                    {coverageOpen && (
                      <ErrorBoundary label="Capture coverage">
                        <CoveragePanel
                          underlying={index}
                          tradeDate={effectiveAsOf}
                          runId={effectiveRunId ?? undefined}
                        />
                      </ErrorBoundary>
                    )}
                  </Stack>
                </article>
              </Stack>
            );
          })()}
      </AsyncBlock>
    </Stack>
  );
}

// How well the fitted surface tracks the quotes that were actually traded, in vol points. The fitter
// records a per-slice fit error (iv_rmse, a vol-point RMSE) on every slice it fit; a slice with no
// fitted surface, or an older read, carries none. We surface the BEST (smallest) fit error across
// the slices that have one, with a count of how many slices the fit covers, so a PM sees at a glance
// how tightly the surface sits on the market. When no slice carries a fit error we say so plainly,
// never inventing a number. A vol point is one hundredth of an annualized vol (so 0.0005 IV = 0.05
// vol pts); the InfoDot spells that out in plain words.
function SurfaceFitPill({ maturities }: { maturities: AnalyticsMaturity[] }) {
  const fits = maturities
    .map((m) => m.surface_slice?.diagnostics?.iv_rmse)
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  const body =
    "How tightly the fitted surface sits on the quotes that actually traded, measured in vol points " +
    "(one vol point is one hundredth of an annualized volatility). Smaller is a closer fit. Shown only " +
    "for the maturities the fitter actually fit; a maturity with no fitted slice, or an older capture, " +
    "carries no fit number.";
  if (fits.length === 0) {
    return (
      <span className="status surface-fit" data-tone="muted">
        fit not available
        <InfoDot label="Surface fit, how to read it" body={body} />
      </span>
    );
  }
  const bestVolPts = Math.min(...fits) * 100;
  const sliceNote = fits.length === 1 ? "1 maturity" : `${fits.length} maturities`;
  return (
    <span className="status surface-fit" data-tone="full">
      {`fit ${bestVolPts.toFixed(2)} vol pts (${sliceNote})`}
      <InfoDot label="Surface fit, how to read it" body={body} />
    </span>
  );
}

// Strict ⟷ Indicative toggle (MAT-LEGIBILITY-strict-indicative-mode). Strict is the default and the
// only stored/tradeable surface (two-sided quotes only); indicative is a view-time overlay that
// includes one-sided/last marks, unmistakably badged so it can never be confused for the close. The
// toggle says what each mode does — the consequence is shown, not sold — and a `mode` change is a
// deliberate act that visibly reframes the page (the INDICATIF badge appears, the coverage numerator
// rises), never a silent data swap.
// Clean surface vs all slices. Default clean drops the front-week / degenerate slices the fitter
// flagged, the ones that draw the impossible delta spike. "All slices, raw" puts them back for a
// deliberate look at a flagged fit. Matches the mode-toggle styling so the two controls read as one
// set. The count of dropped slices is surfaced so the consequence is visible, not hidden.
function CleanSurfaceToggle({
  clean,
  nDropped,
  onChange,
}: {
  clean: boolean;
  nDropped: number;
  onChange: (clean: boolean) => void;
}) {
  const droppedNote =
    nDropped > 0 ? ` (${nDropped} flagged slice${nDropped === 1 ? "" : "s"} hidden)` : "";
  return (
    <div className="mode-toggle" role="group" aria-label="Surface slices">
      <button
        type="button"
        className="mode-toggle__option"
        aria-pressed={clean}
        title="Hide the front-week and degenerate slices the fitter flagged"
        onClick={() => onChange(true)}
      >
        Clean surface{clean ? droppedNote : ""}
      </button>
      <button
        type="button"
        className="mode-toggle__option"
        aria-pressed={!clean}
        title="Include every slice, even the flagged ones that draw the delta spike"
        onClick={() => onChange(false)}
      >
        All slices, raw
      </button>
    </div>
  );
}

// The Call / Put / Combined selector — a first-class control now, not a buried toggle. Calls and
// puts carry genuinely different skew, so each is its own surface; combined is the union the page
// opens on. A side the close did not capture is offered DISABLED (the honest "not captured" state),
// never silently swapped. Shares the clean .mode-toggle pill styling with the other surface controls.
const SURFACE_SIDES_ORDER: SurfaceSide[] = ["combined", "call", "put"];

function SurfaceSideToggle({
  side,
  available,
  perSideServed,
  onChange,
}: {
  side: SurfaceSide;
  available: SurfaceSide[];
  // Whether the payload carries the per-side block at all. False = the backend build predates the
  // per-side surfaces, so the disabled tooltip says "restart the BFF" rather than the misleading
  // "not captured for this close".
  perSideServed: boolean;
  onChange: (side: SurfaceSide) => void;
}) {
  return (
    <div
      className="mode-toggle"
      role="group"
      aria-label="Surface side"
      data-tour-id="market.side-toggle"
    >
      {SURFACE_SIDES_ORDER.map((option) => {
        const captured = available.includes(option);
        const disabledTitle = perSideServed
          ? `${SURFACE_SIDE_LABELS[option]} not captured for this close`
          : `${SURFACE_SIDE_LABELS[option]} needs the per-side surfaces, restart the BFF to enable`;
        return (
          <button
            key={option}
            type="button"
            className="mode-toggle__option"
            aria-pressed={side === option}
            disabled={!captured}
            title={
              captured
                ? option === "combined"
                  ? "Both wings together, the union read"
                  : `The ${SURFACE_SIDE_LABELS[option].toLowerCase()} wing on its own`
                : disabledTitle
            }
            onClick={() => onChange(option)}
          >
            {SURFACE_SIDE_LABELS[option]}
          </button>
        );
      })}
    </div>
  );
}

// The Maturity FLOOR selector — a lower bound on maturity, not a single point. The owner ask: a
// single-maturity pick used to collapse the 3D surface to one slice (a surface needs several
// tenors). This keeps every tenor at or above the chosen floor ("min 1m and up", "min 1y and up",
// ...) so a real surface always renders; the single-tenor 2D smile lives in the Smile & Greeks
// panel below. A plain pill-styled select so it reads as one of the surface controls. Each option's
// `years` is the threshold (0 = no floor); the threshold, not the label, is the value, so two tenors
// that share a label can never collide.
interface MaturityFloorOption {
  years: number;
  label: string;
}

function MaturityFloorSelect({
  value,
  options,
  onChange,
}: {
  value: number;
  options: MaturityFloorOption[];
  onChange: (years: number) => void;
}) {
  return (
    <label className="surface-select">
      <span className="visually-hidden">Minimum maturity</span>
      <select
        aria-label="Minimum maturity"
        title="Lower bound on maturity, keeps every tenor at or above it so the surface stays 3D"
        value={String(value)}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {options.map((option) => (
          <option key={option.years} value={String(option.years)}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function SurfaceModeToggle({
  mode,
  onChange,
}: {
  mode: SurfaceMode;
  onChange: (mode: SurfaceMode) => void;
}) {
  return (
    <div
      className="mode-toggle"
      role="group"
      aria-label="Surface mode"
      data-tour-id="market.mode-toggle"
    >
      <button
        type="button"
        className="mode-toggle__option"
        aria-pressed={mode === "strict"}
        title="Two-sided only, the canonical stored close"
        onClick={() => onChange("strict")}
      >
        Strict, two-sided only
      </button>
      <button
        type="button"
        className="mode-toggle__option"
        aria-pressed={mode === "indicative"}
        title="Includes one-sided marks, estimate, never the stored close"
        onClick={() => onChange("indicative")}
      >
        Indicative, includes one-sided marks
      </button>
    </div>
  );
}
