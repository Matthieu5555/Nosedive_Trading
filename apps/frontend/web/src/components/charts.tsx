import type { Data } from "plotly.js";

import {
  ALL_MATURITIES,
  type AnalyticsMaturity,
  type AnalyticsPoint,
  type PriceHistoryResponse,
  type SurfaceDense,
  type SurfaceSide,
} from "../api";
import { currencySymbol, UNITS, withCurrency } from "../lib/format";
import {
  cleanDenseSurface,
  cleanSmile,
  flaggedNote,
  singleBranchDeltaPoints,
} from "../lib/volRobust";
import { CandleChart } from "./CandleChart";
import { CHART_COLORS, VOL_COLORSCALE } from "./chartTheme";
import { Plot } from "./Plot";

// Two distinct ceilings, split out of the old single `IV_SANE_MAX` reused for both jobs:
//  • the REJECT threshold (data sanity) stays at IV_SANE_MAX (0.6) and lives in volRobust — a cell
//    above it is railed garbage and is clamped to a hole BEFORE plotting.
//  • the DISPLAY colour ceiling is the live SX5E band (~0.35). The nappe's colour scale tops out
//    here so the skew/term structure spreads across the full Plasma ramp instead of being washed
//    into its lower third by the rare 0.6 outlier (the 2026-06-16 bilan finding).
const SURFACE_DISPLAY_Z_MAX = 0.35;

// The z-AXIS still spans the sane band so a tall (but in-band) slice isn't clipped off the top of
// the 3D box; only the COLOUR mapping is compressed to the display ceiling.
const SURFACE_Z_AXIS_MAX = 0.6;

// ---------------------------------------------------------------------------------------------
// Self-describing surface descriptor (MAT-LEGIBILITY-self-describing). One pure builder assembles
// the identity sentence — subject · as-of · mode · coverage — and every label on the nappe block
// (figure caption, empty/error copy, point tooltip) reads the SAME object, so they can never
// disagree. It binds to live state and is "surfacing, not compute": the four facts already exist
// on the props the chart receives; nothing is invented. Missing facts degrade in place (no mode →
// strict, no coverage → "couverture indisponible", no close instant → date only) — the sentence is
// never able to be false for its contents.

export type SurfaceMode = "strict" | "indicative";

// The one coverage fraction, owned by MAT-LEGIBILITY-coverage-headline and computed once in the
// BFF. This is its typed shape as consumed here — never recompute a second fraction. `resting` is
// how many captured quotes the surface actually rests on (strict: two-sided only); `total` is the
// captured chain; `indicative` is how many of `resting` are non-two-sided marks when indicative
// mode is active.
export interface SurfaceCoverage {
  resting: number;
  total: number;
  indicative?: number;
}

export interface SurfaceDescriptorState {
  subject: string | null | undefined;
  asOf: string | null | undefined;
  closeInstant?: string | null;
  mode?: SurfaceMode;
  coverage?: SurfaceCoverage | null;
  degenerate?: boolean;
}

export type SurfaceTone = "full" | "partial" | "degenerate";

export interface SurfaceDescriptor {
  subject: string;
  // The panel heading ("Nappe de volatilité — SX5E"): subject prefixed with the shared noun.
  subjectHeading: string;
  // The caption tail ("clôture 2026-06-17 17:30 CET · strict · 1 706/2 412 cotations"): everything
  // after the heading. The panel renders `<h2>{subjectHeading}</h2>` + `<span>{caption}</span>`;
  // the figure caption renders the full `title`. All three are assembled here, once.
  caption: string;
  title: string;
  asOfPhrase: string;
  modeWord: SurfaceMode;
  coveragePhrase: string;
  tone: SurfaceTone;
  emptyCopy: string;
}

// Integer grouping with the comma thousands separator, the separator the spec sentence
// shows ("1 706/2 412"). Pinned explicitly rather than left to a host locale so the rendered
// string is deterministic across environments.
const THOUSANDS_SEP = ",";
function groupInt(value: number): string {
  return String(Math.trunc(value)).replace(/\B(?=(\d{3})+(?!\d))/g, THOUSANDS_SEP);
}

const SURFACE_SUBJECT = "Volatility surface";

export function describeSurface(state: SurfaceDescriptorState): SurfaceDescriptor {
  const subject = state.subject?.trim() || "unknown";
  const mode: SurfaceMode = state.mode ?? "strict";
  const degenerate = state.degenerate ?? false;

  // As-of clause: carry the close instant when known ("clôture 2026-06-17 17:30 CET"), else the
  // bare date ("clôture 2026-06-17"). Never guess an instant — a wrong instant is the confident
  // lie this descriptor exists to kill (SX5E close is 17:30 CET, not 22:00).
  const date = state.asOf?.trim() || "unknown date";
  const asOfPhrase = state.closeInstant?.trim()
    ? `close ${date} ${state.closeInstant.trim()}`
    : `close ${date}`;

  // Coverage clause: the one shared fraction, never refabricated. Absent → say so plainly.
  let coveragePhrase: string;
  if (state.coverage == null) {
    coveragePhrase = "coverage unavailable";
  } else {
    const { resting, total, indicative } = state.coverage;
    const frac = `${groupInt(resting)}/${groupInt(total)}`;
    coveragePhrase =
      mode === "indicative" && indicative != null && indicative > 0
        ? `${frac} (${groupInt(indicative)} indicative marks)`
        : `${frac} quotes`;
  }

  let tone: SurfaceTone;
  if (degenerate) tone = "degenerate";
  else if (mode === "indicative") tone = "partial";
  else if (state.coverage != null && state.coverage.resting < state.coverage.total)
    tone = "partial";
  else tone = "full";

  // The mode word in the sentence: quiet "strict", loud "INDICATIF" (the badge spec owns the visual
  // badge; this owns the word). On a degenerate close the mode word gives way to the plain-words
  // alarm "indicative — marché probablement fermé".
  const modeClause = degenerate
    ? "indicative, market probably closed"
    : mode === "indicative"
      ? `INDICATIVE · ${coveragePhrase}`
      : `strict · ${coveragePhrase}`;

  const subjectHeading = `${SURFACE_SUBJECT}, ${subject}`;
  const caption = `${asOfPhrase} · ${modeClause}`;
  const title = `${subjectHeading} · ${caption}`;

  // Empty/error copy names its subject and as-of off the SAME descriptor as the populated state.
  // The degenerate (market-probably-closed) copy is the normative §2b sentence: it names the missing
  // thing in PM register ("aucune cotation deux-faces") + the loud market-closed cause — the exact
  // canary the legibility theme exists to surface.
  const emptyCopy = degenerate
    ? `No two-sided quote for ${subject} on ${date}, market probably closed.`
    : `No surface for ${subject} on ${date}.`;

  return {
    subject,
    subjectHeading,
    caption,
    title,
    asOfPhrase,
    modeWord: mode,
    coveragePhrase,
    tone,
    emptyCopy,
  };
}

// Per-point provenance (MAT-LEGIBILITY-strict-indicative-mode). A point is two-sided ("deux-faces")
// when both bid and ask were observed; a one-sided/last mark is an "marque indicative à une face".
// In a strict-only payload (no quote, or a complete two-sided quote) every plotted point is
// two-sided, so the tooltip says "deux-faces" rather than inventing an indicative tag.
function pointProvenance(point: AnalyticsPoint): string {
  const quote = point.quote;
  if (quote && (quote.bid == null || quote.ask == null)) {
    return "one-sided indicative mark";
  }
  return "two-sided";
}

export function PriceChart({ data }: { data: PriceHistoryResponse }) {
  const label = `${data.underlying}, daily price (OHLC candlestick)`;
  if (data.n_bars === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No daily bars for {data.underlying} in this window.</p>
      </figure>
    );
  }
  return <CandleChart bars={data.bars} label={label} />;
}

// Axis-title units, in the house idiom (lib/format.ts UNITS). An axis title is `label (unit)`; an
// unlabeled or unitless axis is a bug. The trader-unit TICK formats (.2f/.0%/.2s) are kept as-is —
// this carries the unit on the *title*, never on the ticks.
const AXIS_LOG_MONEYNESS = `log-moneyness (${UNITS.logMoneyness})`;
const AXIS_IMPLIED_VOL = `implied vol (${UNITS.vol})`;
const AXIS_MATURITY_YEARS = `maturity (${UNITS.years})`;
const AXIS_MATURITY = "maturity";
function axisStrike(currency: string | null | undefined): string {
  return `strike (${withCurrency(UNITS.strike, currencySymbol(currency))})`;
}

const SURFACE_HOW_TO_READ = "implied vol vs log-moneyness vs maturity";

// The ATM ridge — the spine of the surface: a thin amber line tracing the at-the-money implied vol
// (log-moneyness nearest 0) across every maturity. It is the line a vol trader reads first, and the
// one bespoke gesture that makes the nappe "ours". Appended as the SECOND trace (never the first —
// the Plot test mock reads data[0].z). Null/holey ATM cells are skipped; needs ≥2 points to draw.
function atmRidgeTrace(cleaned: ReturnType<typeof cleanDenseSurface>): Data | null {
  const k = cleaned.logMoneyness;
  if (k.length === 0) return null;
  let atmIdx = 0;
  for (let i = 1; i < k.length; i += 1) {
    if (Math.abs(k[i]) < Math.abs(k[atmIdx])) atmIdx = i;
  }
  const x: number[] = [];
  const y: number[] = [];
  const z: number[] = [];
  cleaned.maturityYears.forEach((maturity, i) => {
    const iv = cleaned.impliedVol[i]?.[atmIdx];
    if (typeof iv === "number" && Number.isFinite(iv)) {
      x.push(k[atmIdx]);
      y.push(maturity);
      z.push(iv);
    }
  });
  if (z.length < 2) return null;
  return {
    type: "scatter3d",
    mode: "lines",
    x,
    y,
    z,
    name: "ATM",
    line: { color: CHART_COLORS.amber, width: 5 },
    hoverinfo: "skip",
  } as Data;
}

// The figure caption is the descriptor's identity sentence + the one-line how-to-read gloss, with
// the existing "⚠ {note}" flagged-fit suffix preserved. The identity LEADS; the gloss follows.
function surfaceLabel(descriptor: SurfaceDescriptor, note: string | null): string {
  const base = `${descriptor.title}, ${SURFACE_HOW_TO_READ}`;
  return note ? `${base}, ⚠ ${note}` : base;
}

// Shared props that carry the live state into every chart on the nappe block, so the descriptor is
// assembled once and threaded down — no second place a title is built. All optional: a caller that
// hasn't wired mode/coverage yet still gets a self-describing subject·as-of title that degrades to
// "couverture indisponible" / "strict", never to a generic noun and never to an invented value.
export interface SurfaceIdentityProps {
  subject?: string | null;
  asOf?: string | null;
  closeInstant?: string | null;
  mode?: SurfaceMode;
  coverage?: SurfaceCoverage | null;
  currency?: string | null;
}

function denseHasZ(surface: SurfaceDense): boolean {
  return surface.maturity_years.length > 0 && surface.log_moneyness.length > 0;
}

// A surface needs at least two maturity rows to draw: Plotly `type:"surface"` over a single z-row
// renders nothing (just empty axes). The maturity FLOOR control trims the short end of the dense
// grid, so it must never trim below this minimum.
const MIN_SURFACE_ROWS = 2;

export interface FloorSliceResult {
  surface: SurfaceDense;
  // The floor that was actually applied after the clamp. Equals the requested floor unless honouring
  // it would have left <2 rows, in which case it is relaxed down to the highest floor that keeps two
  // rows (0 = no floor relaxed all the way back to the full span).
  appliedFloorYears: number;
  // True when the requested floor was relaxed to keep the surface ≥2 rows, so the caller can surface
  // an honest inline note rather than silently ignoring or silently blanking the request.
  relaxed: boolean;
  // How many short-end rows the applied floor dropped, for an honest "showing the longer N tenors"
  // style note when wanted.
  nDropped: number;
}

// Slice a dense (maturity × log-moneyness) grid down to the maturity rows at or above `floorYears`,
// keeping x (log-moneyness) untouched and carrying the matching rows of implied_vol and the
// degenerate-maturity list along. The GUARD: a surface needs ≥2 rows to draw, so if the requested
// floor would leave fewer, the floor is relaxed (clamped) up only as far as keeps the highest two
// rows, and `relaxed` is set so the caller can say the floor was eased. floorYears 0 (or absent) is
// "no floor" and returns the grid unchanged. Pure: builds a new SurfaceDense, never mutates input.
export function floorSliceDenseSurface(
  surface: SurfaceDense,
  floorYears: number,
): FloorSliceResult {
  const totalRows = surface.maturity_years.length;
  // No floor, an empty/too-small grid, or a grid that can't be trimmed: pass through untouched.
  if (floorYears <= 0 || totalRows <= MIN_SURFACE_ROWS) {
    return { surface, appliedFloorYears: 0, relaxed: false, nDropped: 0 };
  }
  // The row indices the requested floor keeps (maturity at or above the floor), in served order.
  const requestedKept = surface.maturity_years
    .map((years, i) => ({ years, i }))
    .filter(({ years }) => years >= floorYears);

  let keptIdx: number[];
  let relaxed = false;
  let appliedFloorYears = floorYears;
  if (requestedKept.length >= MIN_SURFACE_ROWS) {
    keptIdx = requestedKept.map(({ i }) => i);
  } else {
    // Honouring the floor would leave <2 rows. Clamp: keep the highest MIN_SURFACE_ROWS rows so a
    // real surface still draws, and report the relaxation honestly. The applied floor becomes the
    // lowest maturity we ended up keeping.
    relaxed = true;
    const byYearsDesc = surface.maturity_years
      .map((years, i) => ({ years, i }))
      .sort((a, b) => b.years - a.years)
      .slice(0, MIN_SURFACE_ROWS);
    keptIdx = byYearsDesc.map(({ i }) => i).sort((a, b) => a - b);
    appliedFloorYears = Math.min(...byYearsDesc.map(({ years }) => years));
  }

  const keptYears = keptIdx.map((i) => surface.maturity_years[i]);
  const keptSet = new Set(keptYears);
  // Slice BOTH grids on the SAME row indices so the clamped and filled grids stay on identical axes;
  // the chart then picks one by `filled` and the floor sees the same rows either way.
  const filledRows = surface.implied_vol_filled;
  return {
    surface: {
      ...surface,
      maturity_years: keptYears,
      implied_vol: keptIdx.map((i) => surface.implied_vol[i] ?? []),
      ...(filledRows
        ? { implied_vol_filled: keptIdx.map((i) => filledRows[i] ?? []) }
        : {}),
      degenerate_maturity_years: surface.degenerate_maturity_years.filter((y) => keptSet.has(y)),
    },
    appliedFloorYears,
    relaxed,
    nDropped: totalRows - keptIdx.length,
  };
}

// Preferred path: the dense surface reconstructed from the fitted SVI slices (the blueprint's
// regularized grid), served by the BFF. It is already a smooth (maturity × log-moneyness) lattice
// of implied vol, so it plots as the smooth fitted model — no kinks from a sparse delta-band
// polyline. y is maturity in *years* (a real continuous axis; the dense grid never bunches the way
// the 8 raw tenors did, so the index hack the fallback needs is unnecessary here).
function DenseVolSurface({
  surface,
  descriptor,
  floored,
  filled,
}: {
  surface: SurfaceDense;
  descriptor: SurfaceDescriptor;
  // The floor-slice outcome, so a relaxed (clamped) floor surfaces an honest inline note instead of
  // silently ignoring the request. Absent / not relaxed → no note.
  floored?: FloorSliceResult | null;
  // CLEAN (true) renders the FILLED, capped-at-0.60 grid (implied_vol_filled): the classic smooth
  // nappe with no holes. RAW (false) renders the CLAMPED/holey grid (implied_vol): the honest, less
  // interpolated surface with gaps where strikes stop. When filled is asked for but the payload
  // predates implied_vol_filled, we fall back to the clamped grid rather than draw nothing.
  filled: boolean;
}) {
  // Robustness (render layer only — the served values are never mutated): a railed slice serves
  // absurd IVs (108%, 140% at deep-OTM deltas) and duplicate log-moneyness columns; left raw they
  // spike the nappe's height and stretch its colour band. Clamp out-of-band / non-finite cells to
  // null holes (NOT bridged — holes show where coherence breaks, §4.5) and collapse duplicate-k
  // columns, then surface an honest count of the flagged slices instead of rendering the garbage peak.
  // Pick the grid first (filled = clean, smooth, no holes; else the clamped, holey grid), then run
  // the SAME cleaning pass on whichever was chosen. The filled grid is already <=0.60 so cleaning
  // nulls nothing; the clamped grid keeps its holes.
  const chosenGrid =
    filled && surface.implied_vol_filled ? surface.implied_vol_filled : surface.implied_vol;
  const cleaned = cleanDenseSurface(surface.log_moneyness, surface.maturity_years, chosenGrid);
  const note = flaggedNote(cleaned.nFlaggedSlices, "slice");
  // The colour scale tops out at the DISPLAY ceiling (the live SX5E band) so the skew/term reads
  // across the full house ramp; a rare in-band-but-tall slice still draws (the z-axis spans wider),
  // it just saturates the top colour rather than washing every normal cell into the lower ramp.
  const trace = {
    type: "surface",
    x: cleaned.logMoneyness,
    y: cleaned.maturityYears,
    z: cleaned.impliedVol,
    name: "IV surface",
    colorscale: VOL_COLORSCALE,
    cmin: 0,
    cmax: SURFACE_DISPLAY_Z_MAX,
    connectgaps: false,
    colorbar: { title: { text: "IV" }, tickformat: ".0%" },
    // Point tooltip: real coordinates + provenance. A dense (fitted) cell is the model surface, so
    // every cell is the strict two-sided fit; the hovertemplate names the axis values with units.
    hovertemplate:
      `log-moneyness %{x:.3f} ${UNITS.logMoneyness}<br>` +
      `maturity %{y:.2f} ${UNITS.years}<br>` +
      "implied vol %{z:.1%} · two-sided<extra></extra>",
  } as Data;
  const ridge = atmRidgeTrace(cleaned);
  // An honest inline note when the maturity floor was relaxed to keep the surface ≥2 rows: the
  // request is acknowledged and the easing is shown, never a silent ignore and never a blank chart.
  // The same fact rides the figure label so the descriptor sentence and the note can't disagree.
  const relaxedNote = floored?.relaxed
    ? "Maturity floor eased so the surface keeps at least two tenors."
    : null;
  const label = relaxedNote
    ? `${surfaceLabel(descriptor, note)}, ${relaxedNote}`
    : surfaceLabel(descriptor, note);
  return (
    <>
      {relaxedNote && (
        <p className="state-panel state-panel--note" role="status">
          {relaxedNote}
        </p>
      )}
      <Plot
        label={label}
        height={480}
        data={ridge ? [trace, ridge] : [trace]}
        layout={{
          scene: {
            xaxis: { title: { text: AXIS_LOG_MONEYNESS } },
            yaxis: { title: { text: AXIS_MATURITY_YEARS } },
            zaxis: { title: { text: AXIS_IMPLIED_VOL }, range: [0, SURFACE_Z_AXIS_MAX] },
            aspectmode: "manual",
            aspectratio: { x: 1.4, y: 1.5, z: 0.7 },
            camera: { eye: { x: 1.8, y: -1.8, z: 0.8 } },
          },
        }}
      />
    </>
  );
}

export function VolSurface({
  surface,
  maturities,
  floorYears = 0,
  filled = true,
  subject,
  asOf,
  closeInstant,
  mode,
  coverage,
}: {
  surface?: SurfaceDense | null;
  maturities: AnalyticsMaturity[];
  // A lower bound on maturity (in years), the maturity-floor control. 0 (or absent) = no floor. The
  // dense path slices the grid to rows at or above it, clamping so ≥2 rows always remain so the 3D
  // surface never blanks; a relaxed floor surfaces an honest inline note rather than failing silently.
  floorYears?: number;
  // CLEAN (true, the default) draws the smooth FILLED nappe (implied_vol_filled); RAW (false) draws
  // the honest CLAMPED/holey grid (implied_vol). Only changes interpolation/fill, not which tenors
  // show: the floor slices whichever grid is chosen on the SAME axes. Dense path only.
  filled?: boolean;
} & SurfaceIdentityProps) {
  const hasDense = !!surface && denseHasZ(surface);
  // Apply the maturity floor to the dense grid: slice to rows at or above it, clamping so ≥2 rows
  // always remain (the guard, so the surface never blanks). The faithful dense surface is sliced
  // here rather than dropped to the coarse fallback, so a floor actually trims the short end of the
  // SAME fitted grid instead of forcing a blank or a different render path (BUG #3).
  const floored = hasDense ? floorSliceDenseSurface(surface!, floorYears) : null;
  const flooredSurface = floored?.surface ?? null;
  // A degenerate close (the market-probably-closed surface) is the descriptor's loud state. The
  // dense grid flags it per-maturity; in the fallback path it rides the per-slice degenerate flag.
  const degenerateClose =
    (!!flooredSurface &&
      flooredSurface.degenerate_maturity_years.length === flooredSurface.maturity_years.length) ||
    (!hasDense &&
      maturities.length > 0 &&
      maturities.every((m) => m.surface_slice?.degenerate ?? false));
  const descriptor = describeSurface({
    subject,
    asOf,
    closeInstant,
    mode,
    coverage,
    degenerate: degenerateClose,
  });
  // Render the smooth reconstructed surface whenever the fit produced one; otherwise fall back to
  // the coarse grid built from the sparse delta-band points below (e.g. a single fitted slice, or
  // the surface-grid fallback with no fit).
  if (flooredSurface) {
    return (
      <DenseVolSurface
        surface={flooredSurface}
        descriptor={descriptor}
        floored={floored}
        filled={filled}
      />
    );
  }
  // A clean rectangular vol surface: x = log-moneyness, y = the maturity *index* (0,1,2…),
  // z = implied vol. The x axis is ALWAYS log-moneyness (carried in both smile modes), never the
  // signed-delta axis: signed delta is not monotone in strike — a deep-OTM put (high IV) lands
  // next to ATM (low IV), which folded every smile into an artificial spike at the middle of the
  // axis. The maturity axis is an even index, not calendar years — in years the short tenors
  // bunch near zero and the mesh looks spiky; an even index lays the surface flat and regular.
  // Plotly `surface` over a (maturity × x) z-grid reads as a true surface, not a mesh3d cloud.
  // A missing (x, maturity) cell is a null hole, bridged only visually by connectgaps.
  // Clean each slice (drop non-finite / out-of-band IV + duplicate-k points) BEFORE building the
  // z-grid, so a railed fallback slice cannot spike the surface — same render-only policy as the
  // dense path. The served values are untouched; only the plotted geometry is cleaned.
  const cleaned = [...maturities]
    .map((maturity) => ({
      maturity,
      clean: cleanSmile(maturity.smile.log_moneyness, maturity.smile.implied_vols),
    }))
    .filter(({ clean }) => clean.logMoneyness.length > 0)
    .sort((a, b) => a.maturity.maturity_years - b.maturity.maturity_years);
  const nFlaggedSlices = cleaned.filter(
    ({ clean }) => clean.nDroppedAbsurd + clean.nDroppedNonFinite > 0,
  ).length;
  const note = flaggedNote(nFlaggedSlices, "slice");
  const label = surfaceLabel(descriptor, note);
  if (cleaned.length === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p role="status">{descriptor.emptyCopy}</p>
      </figure>
    );
  }
  const sorted = cleaned.map(({ maturity }) => maturity);
  // Common x grid (union of every maturity's CLEANED log-moneyness axis), so the z-grid stays
  // rectangular even where a coarse long-dated tenor lacks the wing bands.
  const xGrid = [...new Set(cleaned.flatMap(({ clean }) => clean.logMoneyness))].sort(
    (a, b) => a - b,
  );
  const z: (number | null)[][] = cleaned.map(({ clean }) => {
    const byK = new Map(clean.logMoneyness.map((k, i) => [k, clean.impliedVols[i]]));
    return xGrid.map((k) => byK.get(k) ?? null);
  });
  const yIndex = sorted.map((_, i) => i);
  // cmin/cmax lock the colour mapping to the same fixed band as the z-axis, so a given colour
  // means the same IV regardless of the day's min/max — coherent with the pinned z range below.
  // plotly.js honours cmin/cmax on `surface` at runtime; the bundled TS types omit them, hence
  // the assertion.
  const trace = {
    type: "surface",
    x: xGrid,
    y: yIndex,
    z,
    name: "IV surface",
    colorscale: VOL_COLORSCALE,
    cmin: 0,
    cmax: SURFACE_DISPLAY_Z_MAX,
    connectgaps: false,
    colorbar: { title: { text: "IV" }, tickformat: ".0%" },
    hovertemplate:
      `log-moneyness %{x:.3f} ${UNITS.logMoneyness}<br>` +
      "implied vol %{z:.1%} · two-sided<extra></extra>",
  } as Data;
  return (
    <Plot
      label={label}
      height={480}
      data={[trace]}
      layout={{
        scene: {
          xaxis: { title: { text: AXIS_LOG_MONEYNESS } },
          yaxis: {
            title: { text: AXIS_MATURITY },
            tickvals: yIndex,
            ticktext: sorted.map((maturity) => maturity.tenor_label || maturity.label),
          },
          // Pinned, zero-anchored z-axis: the surface stops re-zooming itself across dates.
          zaxis: { title: { text: AXIS_IMPLIED_VOL }, range: [0, SURFACE_Z_AXIS_MAX] },
          // Lay the surface flat (compressed z) rather than a cube, so the skew/term structure
          // reads at a glance instead of a tall spiky block.
          aspectmode: "manual",
          aspectratio: { x: 1.4, y: 1.5, z: 0.7 },
          camera: { eye: { x: 1.8, y: -1.8, z: 0.8 } },
        },
      }}
    />
  );
}

// Smile wing colours: puts (downside) read red, calls (upside) green — the convention an operator
// expects, off the shared --negative / --positive design tokens.
const PUT_COLOR = CHART_COLORS.negative;
const CALL_COLOR = CHART_COLORS.positive;

const SMILE_HOW_TO_READ = "implied vol vs log-moneyness ; puts ◄ ATM ► calls";
// A single-side smile is one continuous curve (every strike priced off that side's quote), not two
// wings; its how-to-read says so, so a Puts-only smile reading high at low strikes is unmistakably
// "deep out-of-the-money puts, high IV" rather than a half-labelled combined curve.
const SMILE_HOW_TO_READ_SIDE: Record<"put" | "call", string> = {
  put: "implied vol vs log-moneyness ; put-quoted, deep OTM puts ◄ (low strikes, high IV)",
  call: "implied vol vs log-moneyness ; call-quoted, deep OTM calls ► (high strikes)",
};

// Trader-unit ticks: log-moneyness as a plain decimal k (not -3.00e-1), IV as a percent. The old
// ".2e" scientific formatting on both axes was unreadable for an operator (the 2026-06-16 bilan).
const SMILE_LAYOUT = {
  xaxis: { title: { text: AXIS_LOG_MONEYNESS }, zeroline: true, tickformat: ".2f" },
  yaxis: { title: { text: AXIS_IMPLIED_VOL }, rangemode: "tozero" as const, tickformat: ".0%" },
  legend: { orientation: "h" as const, y: -0.22 },
  hovermode: "closest" as const,
};

// The smile for ONE tenor. For the COMBINED view (default): the put wing (k ≤ 0, red) and the call
// wing (k ≥ 0, green) SUPERIMPOSED on a shared log-moneyness axis, joining at ATM (k = 0). The two
// curves read whole — the vertical gap between the wings IS the skew (ADR 0048 per-side overlay; the
// `combined` shape is the union of both). For a single SIDE (put or call): every strike is priced
// off that side's quote, so it is ONE continuous curve coloured for that side and labelled as such,
// NOT split into pseudo-wings by the sign of k — a Puts-only smile rises into the deep-OTM-put (low
// strike, left) wing exactly as an operator expects. A tenor the capture didn't reach is handled
// upstream as a labelled gap; here an empty smile is an honest empty state, never a blank.
export function SmileChart({
  maturities,
  maturityLabel,
  subject,
  asOf,
  closeInstant,
  mode,
  coverage,
  side = "combined",
}: {
  maturities: AnalyticsMaturity[];
  // The selected tenor's label. Falls back to the front tenor when the label isn't found.
  maturityLabel?: string;
  // The surface side in view. Combined splits into put/call wings; a single side is one curve.
  side?: SurfaceSide;
} & SurfaceIdentityProps) {
  const sorted = [...maturities].sort((a, b) => a.maturity_years - b.maturity_years);
  const singleSide = side === "put" || side === "call";
  const howToRead = singleSide ? SMILE_HOW_TO_READ_SIDE[side] : SMILE_HOW_TO_READ;
  // The smile shares the surface's identity sentence; the tenor is appended as the smile's own
  // subject detail so the title still answers "which underlying, which date, which mode" first.
  const baseDescriptor = describeSurface({ subject, asOf, closeInstant, mode, coverage });

  if (sorted.length === 0) {
    const label = `${baseDescriptor.title}, smile, ${howToRead}`;
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p role="status">{baseDescriptor.emptyCopy}</p>
      </figure>
    );
  }

  const maturity = sorted.find((m) => m.label === maturityLabel) ?? sorted[0];
  const degenerate = maturity.surface_slice?.degenerate ?? false;
  const clean = cleanSmile(maturity.smile.log_moneyness, maturity.smile.implied_vols);
  const nDropped = clean.nDroppedNonFinite + clean.nDroppedAbsurd + clean.nDroppedDuplicate;
  const dropNote = nDropped > 0 ? `, ${nDropped} pt${nDropped === 1 ? "" : "s"} flagged` : "";
  const label = `${baseDescriptor.title}, smile ${maturity.label} (${howToRead})${
    degenerate ? " ⚠ degenerate fit" : ""
  }${dropNote}`;

  const provByK = new Map(maturity.points.map((p) => [p.log_moneyness, pointProvenance(p)]));
  const putPairs: Array<[number, number]> = [];
  const callPairs: Array<[number, number]> = [];
  if (singleSide) {
    // One side: the whole curve is that side, every strike. No sign-of-k split (that would mislabel
    // this side's high-strike quotes as the other side). Sorted left (deep OTM puts) to right.
    const pairs = clean.logMoneyness.map((k, i): [number, number] => [k, clean.impliedVols[i]]);
    if (side === "put") putPairs.push(...pairs);
    else callPairs.push(...pairs);
  } else {
    clean.logMoneyness.forEach((k, i) => {
      if (k <= 0) putPairs.push([k, clean.impliedVols[i]]);
      if (k >= 0) callPairs.push([k, clean.impliedVols[i]]);
    });
  }
  putPairs.sort((a, b) => a[0] - b[0]);
  callPairs.sort((a, b) => a[0] - b[0]);
  const wingTrace = (name: string, color: string, pairs: Array<[number, number]>): Data => ({
    type: "scatter",
    mode: "lines+markers",
    name,
    x: pairs.map((p) => p[0]),
    y: pairs.map((p) => p[1]),
    // Point tooltip: real coordinates (k, IV) + per-point provenance (deux-faces vs marque
    // indicative à une face), reading the per-strike quote on the matching point.
    text: pairs.map(([k]) => provByK.get(k) ?? "two-sided"),
    hovertemplate:
      `${name} · log-moneyness %{x:.3f} ${UNITS.logMoneyness}<br>` +
      "implied vol %{y:.1%} · %{text}<extra></extra>",
    line: { color, width: 2 },
    marker: { color, size: 5 },
  });
  const traces: Data[] = [];
  if (putPairs.length > 0) traces.push(wingTrace("puts", PUT_COLOR, putPairs));
  if (callPairs.length > 0) traces.push(wingTrace("calls", CALL_COLOR, callPairs));
  if (traces.length === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p role="status">{baseDescriptor.emptyCopy}</p>
      </figure>
    );
  }
  return <Plot label={label} height={360} data={traces} layout={SMILE_LAYOUT} />;
}

const GREEKS_SHAPE_HOW_TO_READ =
  "Greeks vs strike ; gamma/vega bell, call-delta S-curve from near 1 (low strikes) to near 0 (high strikes)";

const GREEKS_SHAPE_LAYOUT = {
  yaxis: { title: { text: "call delta (S-curve)" }, zeroline: true, tickformat: ".2f" },
  yaxis2: {
    title: { text: "gamma / vega (bell)" },
    overlaying: "y" as const,
    side: "right" as const,
    showgrid: false,
  },
  legend: { orientation: "h" as const, y: -0.22 },
  hovermode: "closest" as const,
};

export function GreeksShapeCurves({
  maturities,
  maturityLabel,
  subject,
  asOf,
  closeInstant,
  mode,
  coverage,
  currency,
}: {
  maturities: AnalyticsMaturity[];
  maturityLabel?: string;
} & SurfaceIdentityProps) {
  const baseDescriptor = describeSurface({ subject, asOf, closeInstant, mode, coverage });
  const layout = {
    ...GREEKS_SHAPE_LAYOUT,
    xaxis: { title: { text: axisStrike(currency) }, tickformat: ".2s" },
  };
  if (maturities.length === 0) {
    const label = `${baseDescriptor.title}, Greeks, ${GREEKS_SHAPE_HOW_TO_READ}`;
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p role="status">{baseDescriptor.emptyCopy}</p>
      </figure>
    );
  }

  const isAll = maturityLabel === ALL_MATURITIES || maturityLabel === undefined;
  const frontMaturity = [...maturities].sort((a, b) => a.maturity_years - b.maturity_years)[0];
  const maturity = isAll
    ? frontMaturity
    : (maturities.find((m) => m.label === maturityLabel) ?? frontMaturity);

  // A single smile carries both wings (put-quoted low strikes, call-quoted high strikes) with the
  // at-the-money strike duplicated. singleBranchDeltaPoints dedupes by strike and rewrites delta onto
  // one (call) convention, so the delta trace is a single continuous S-curve instead of an impossible
  // vertical spike where the put and call branches cross at the money. Gamma/vega read the same
  // deduplicated set so all three curves share one strike axis.
  const points: AnalyticsPoint[] = singleBranchDeltaPoints(maturity.points);
  const label = `${baseDescriptor.title}, Greeks ${maturity.label} (${GREEKS_SHAPE_HOW_TO_READ})`;
  if (points.length === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p role="status">{baseDescriptor.emptyCopy}</p>
      </figure>
    );
  }

  const strikes = points.map((p) => p.strike);
  const deltaCurve: Data = {
    type: "scatter",
    mode: "lines+markers",
    name: "delta",
    x: strikes,
    y: points.map((p) => p.metrics.delta.raw),
    yaxis: "y",
    line: { color: CHART_COLORS.positive, width: 2 },
    marker: { color: CHART_COLORS.positive, size: 4 },
    hovertemplate: `strike %{x:.4s}<br>delta %{y:.4f} ${UNITS.delta}<extra>delta</extra>`,
  };
  const gammaCurve: Data = {
    type: "scatter",
    mode: "lines+markers",
    name: "gamma",
    x: strikes,
    y: points.map((p) => p.metrics.gamma.raw),
    yaxis: "y2",
    line: { color: CHART_COLORS.muted, width: 2 },
    marker: { color: CHART_COLORS.muted, size: 4 },
    hovertemplate: `strike %{x:.4s}<br>gamma %{y:.4g} ${UNITS.gamma}<extra>gamma</extra>`,
  };
  const vegaCurve: Data = {
    type: "scatter",
    mode: "lines+markers",
    name: "vega",
    x: strikes,
    y: points.map((p) => p.metrics.vega.raw),
    yaxis: "y2",
    line: { color: CHART_COLORS.negative, width: 2, dash: "dot" },
    marker: { color: CHART_COLORS.negative, size: 4 },
    hovertemplate: `strike %{x:.4s}<br>vega %{y:.4g} ${UNITS.vega}<extra>vega</extra>`,
  };

  return (
    <Plot label={label} height={360} data={[deltaCurve, gammaCurve, vegaCurve]} layout={layout} />
  );
}
