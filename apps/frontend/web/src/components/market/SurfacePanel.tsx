import { type AnalyticsMaturity, type SurfaceDense, type SurfaceSide } from "../../api";
import { tourAnchor } from "../../lib/tour";
import { AsyncBlock } from "../AsyncBlock";
import {
  type SurfaceCoverage,
  type SurfaceDescriptor,
  type SurfaceMode,
  VolSurface,
} from "../charts";
import { ErrorBoundary } from "../ErrorBoundary";
import { InfoDot } from "../InfoDot";
import { Cluster, Stack } from "../layout";
import { SideToggle } from "./SideToggle";

// The Volatility surface element: the 3D surface plus the four first-class surface controls (side,
// maturity floor, clean/raw fill, strict/indicative). Self-contained, with its own heading, async +
// error boundary, and an honest empty/closed-market state. Everything it shows is threaded in by the
// page; it owns no fetch and no analytics math.
export function SurfacePanel({
  descriptor,
  surfaceMode,
  surfaceSide,
  sidesAvailable,
  perSideServed,
  perSideFitMissing,
  hasData,
  surfaceMissing,
  maturityFloorYears,
  maturityFloorOptions,
  cleanSurface,
  surfaceSideMaturities,
  surfaceMaturities,
  sideSurface,
  subject,
  asOf,
  closeInstant,
  coverage,
  loading,
  error,
  onSideChange,
  onFloorChange,
  onCleanChange,
  onModeChange,
}: {
  descriptor: SurfaceDescriptor;
  surfaceMode: SurfaceMode;
  surfaceSide: SurfaceSide;
  sidesAvailable: SurfaceSide[];
  perSideServed: boolean;
  perSideFitMissing: boolean;
  // Whether the analytics payload has actually landed. Gates the surface render so it never draws the
  // empty fallback over a not-yet-loaded payload (the AsyncBlock skeleton shows instead).
  hasData: boolean;
  surfaceMissing: boolean;
  maturityFloorYears: number;
  maturityFloorOptions: MaturityFloorOption[];
  cleanSurface: boolean;
  surfaceSideMaturities: AnalyticsMaturity[];
  surfaceMaturities: AnalyticsMaturity[];
  sideSurface: SurfaceDense | null;
  subject: string;
  asOf: string | null;
  closeInstant: string | null;
  coverage: SurfaceCoverage | null;
  loading: boolean;
  error: string | null;
  onSideChange: (side: SurfaceSide) => void;
  onFloorChange: (years: number) => void;
  onCleanChange: (clean: boolean) => void;
  onModeChange: (mode: SurfaceMode) => void;
}) {
  return (
    <article
      className="panel"
      aria-label={descriptor.subjectHeading}
      {...tourAnchor(
        "market.surface",
        "Volatility surface",
        "The 3D implied-volatility surface, vol against moneyness and maturity.",
      )}
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
              <SurfaceFitPill maturities={surfaceSideMaturities} />
            </Cluster>
            <Cluster className="panel-heading__toggles" gap="xs" align="center">
              <SideToggle
                side={surfaceSide}
                available={sidesAvailable}
                perSideServed={perSideServed}
                onChange={onSideChange}
                ariaLabel="Surface side"
                anchor={{
                  id: "market.side-toggle",
                  title: "Surface side toggle",
                  body: "Switch the surface between calls, puts, and both sides combined.",
                }}
              />
              <MaturityFloorSelect
                value={maturityFloorYears}
                options={maturityFloorOptions}
                onChange={onFloorChange}
              />
              <CleanSurfaceToggle clean={cleanSurface} onChange={onCleanChange} />
              <SurfaceModeToggle mode={surfaceMode} onChange={onModeChange} />
            </Cluster>
          </div>
        </div>
        {perSideFitMissing && (
          <p className="state-panel state-panel--note" role="status">
            Per-side fit not available for this close, showing combined.
          </p>
        )}
        {hasData && !perSideServed && (
          <p className="state-panel state-panel--note" role="status">
            Calls / Puts are off because the backend serving this page does not yet return the
            per-side surfaces. Restart the BFF to enable them.
          </p>
        )}
        <ErrorBoundary label="3D surface">
          <AsyncBlock
            loading={loading}
            error={error}
            height={480}
            subject={`the ${subject} surface`}
          >
            {hasData &&
              (surfaceMissing ? (
                <p className="state-panel" role="status">
                  {descriptor.emptyCopy}
                </p>
              ) : (
                <VolSurface
                  surface={sideSurface}
                  floorYears={maturityFloorYears}
                  filled={cleanSurface}
                  maturities={surfaceMaturities}
                  subject={subject}
                  asOf={asOf}
                  closeInstant={closeInstant}
                  mode={surfaceMode}
                  coverage={coverage}
                />
              ))}
          </AsyncBlock>
        </ErrorBoundary>
      </Stack>
    </article>
  );
}

// How well the fitted surface tracks the quotes that were actually traded, in vol points. The fitter
// records a per-slice fit error (iv_rmse, a vol-point RMSE) on every slice it fit; a slice with no
// fitted surface, or an older read, carries none. We surface the BEST (smallest) fit error across
// the slices that have one, with a count of how many slices the fit covers, so a PM sees at a glance
// how tightly the surface sits on the market. When no slice carries a fit error we say so plainly,
// never inventing a number.
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

// Clean vs raw fill of the 3D surface. Clean (default) draws the smooth, fully filled nappe, the
// classic vol-surface look. Raw draws the honest, less-interpolated surface that keeps the holes
// where strikes stop, so you can see exactly where coverage runs out. The toggle changes only the
// interpolation/fill, never which tenors show.
function CleanSurfaceToggle({
  clean,
  onChange,
}: {
  clean: boolean;
  onChange: (clean: boolean) => void;
}) {
  return (
    <div className="mode-toggle" role="group" aria-label="Surface fill">
      <button
        type="button"
        className="mode-toggle__option"
        aria-pressed={clean}
        title="Smooth, fully filled nappe, the classic vol-surface look"
        onClick={() => onChange(true)}
      >
        Clean surface
      </button>
      <button
        type="button"
        className="mode-toggle__option"
        aria-pressed={!clean}
        title="Less interpolated, keeps the gaps where strikes stop"
        onClick={() => onChange(false)}
      >
        Raw, with gaps
      </button>
    </div>
  );
}

// The Maturity FLOOR selector, a lower bound on maturity, not a single point. It keeps every tenor at
// or above the chosen floor ("min 1m and up", "min 1y and up", ...) so a real 3D surface always
// renders; the single-tenor 2D smile lives in the Smile & Greeks panel below. Each option's `years`
// is the threshold (0 = no floor); the threshold, not the label, is the value, so two tenors that
// share a label can never collide.
export interface MaturityFloorOption {
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

// Strict / Indicative toggle. Strict is the default and the only stored/tradeable surface (two-sided
// quotes only); indicative is a view-time overlay that includes one-sided/last marks, unmistakably
// badged so it can never be confused for the close.
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
      {...tourAnchor(
        "market.mode-toggle",
        "Strict and indicative toggle",
        "Switch the surface between strict, two-sided quotes only, and indicative, which adds one-sided marks as an estimate.",
      )}
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
