import { useEffect, useMemo, useState } from "react";

import {
  type AnalyticsResponse,
  type IndicesResponse,
  type PriceHistoryResponse,
  type RecordedDatesResponse,
  type SignalsResponse,
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

  const currency = currencySymbol(indexOptions.find((o) => o.symbol === index)?.currency);

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
                <div className="market-scroll__status">
                  <span className="status">
                    {index} · {descriptor.asOfPhrase} <QcBadge qc={qc} />
                  </span>
                </div>

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

                <article className="panel" aria-label={`${index} daily history`}>
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
                    selected={selectedMember}
                    onSelect={setSelectedMember}
                  />
                </ErrorBoundary>

                <article className="panel" aria-label={descriptor.subjectHeading}>
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
                        <span className="status" data-tone={descriptor.tone}>
                          {descriptor.caption}
                        </span>
                        <SurfaceModeToggle mode={surfaceMode} onChange={setSurfaceMode} />
                      </div>
                    </div>
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
                            <VolSurface
                              surface={analytics.data.surface}
                              maturities={analytics.data.maturities}
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
                        maturities={analytics.data.maturities}
                        currency={currency}
                        subject={index}
                        asOf={effectiveAsOf}
                        closeInstant={instant}
                        mode={surfaceMode}
                        coverage={surfaceCoverage}
                      />
                    )}
                  </AsyncBlock>
                </ErrorBoundary>

                <article className="panel" aria-label="Dispersion">
                  <Stack gap="md">
                    <div className="panel-heading">
                      <Cluster gap="2xs" align="center">
                        <h2>Dispersion (ρ̄), {index}</h2>
                        <InfoDot
                          label="Dispersion, how to read it"
                          body={`How tightly the ${index} members are expected to move together. A high average correlation (ρ̄ near 1) means the index moves as one block, so index vol is dear relative to its members; a low ρ̄ means the members move independently, the case for a dispersion trade. Today a realized-vol diagnostic until constituent implied vols land.`}
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

                <article className="panel" aria-label="Capture coverage">
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

// Strict ⟷ Indicative toggle (MAT-LEGIBILITY-strict-indicative-mode). Strict is the default and the
// only stored/tradeable surface (two-sided quotes only); indicative is a view-time overlay that
// includes one-sided/last marks, unmistakably badged so it can never be confused for the close. The
// toggle says what each mode does — the consequence is shown, not sold — and a `mode` change is a
// deliberate act that visibly reframes the page (the INDICATIF badge appears, the coverage numerator
// rises), never a silent data swap.
function SurfaceModeToggle({
  mode,
  onChange,
}: {
  mode: SurfaceMode;
  onChange: (mode: SurfaceMode) => void;
}) {
  return (
    <div className="mode-toggle" role="group" aria-label="Surface mode">
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
