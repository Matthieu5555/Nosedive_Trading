import type { Data } from "plotly.js";

import {
  ALL_MATURITIES,
  type AnalyticsMaturity,
  type AnalyticsPoint,
  type PriceHistoryResponse,
  type SurfaceDense,
} from "../api";
import { cleanDenseSurface, cleanSmile, flaggedNote } from "../lib/volRobust";
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

export function PriceChart({ data }: { data: PriceHistoryResponse }) {
  const label = `${data.underlying} — daily price (OHLC candlestick)`;
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

const SURFACE_LABEL = "Implied-volatility surface (vol vs log-moneyness vs maturity)";

// Preferred path: the dense surface reconstructed from the fitted SVI slices (the blueprint's
// regularized grid), served by the BFF. It is already a smooth (maturity × log-moneyness) lattice
// of implied vol, so it plots as the smooth fitted model — no kinks from a sparse delta-band
// polyline. y is maturity in *years* (a real continuous axis; the dense grid never bunches the way
// the 8 raw tenors did, so the index hack the fallback needs is unnecessary here).
function DenseVolSurface({ surface }: { surface: SurfaceDense }) {
  // Robustness (render layer only — the served values are never mutated): a railed slice serves
  // absurd IVs (108%, 140% at deep-OTM deltas) and duplicate log-moneyness columns; left raw they
  // spike the nappe's height and stretch its colour band. Clamp out-of-band / non-finite cells to
  // null holes (NOT bridged — holes show where coherence breaks, §4.5) and collapse duplicate-k
  // columns, then surface an honest count of the flagged slices instead of rendering the garbage peak.
  const cleaned = cleanDenseSurface(
    surface.log_moneyness,
    surface.maturity_years,
    surface.implied_vol,
  );
  const note = flaggedNote(cleaned.nFlaggedSlices, "slice");
  // The colour scale tops out at the DISPLAY ceiling (the live SX5E band) so the skew/term reads
  // across the full Plasma ramp; a rare in-band-but-tall slice still draws (the z-axis spans wider),
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
    colorbar: { title: { text: "IV" } },
  } as Data;
  return (
    <Plot
      label={note ? `${SURFACE_LABEL} — ⚠ ${note}` : SURFACE_LABEL}
      height={480}
      data={[trace]}
      layout={{
        scene: {
          xaxis: { title: { text: "log-moneyness" } },
          yaxis: { title: { text: "maturity (years)" } },
          zaxis: { title: { text: "implied vol" }, range: [0, SURFACE_Z_AXIS_MAX] },
          aspectmode: "manual",
          aspectratio: { x: 1.4, y: 1.5, z: 0.7 },
          camera: { eye: { x: 1.8, y: -1.8, z: 0.8 } },
        },
      }}
    />
  );
}

export function VolSurface({
  surface,
  maturities,
}: {
  surface?: SurfaceDense | null;
  maturities: AnalyticsMaturity[];
}) {
  // Render the smooth reconstructed surface whenever the fit produced one; otherwise fall back to
  // the coarse grid built from the sparse delta-band points below (e.g. a single fitted slice, or
  // the surface-grid fallback with no fit).
  if (surface && surface.maturity_years.length > 0 && surface.log_moneyness.length > 0) {
    return <DenseVolSurface surface={surface} />;
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
  const label = note ? `${SURFACE_LABEL} — ⚠ ${note}` : SURFACE_LABEL;
  if (cleaned.length === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No surface to plot yet.</p>
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
    colorbar: { title: { text: "IV" } },
  } as Data;
  return (
    <Plot
      label={label}
      height={480}
      data={[trace]}
      layout={{
        scene: {
          xaxis: { title: { text: "log-moneyness" } },
          yaxis: {
            title: { text: "maturity" },
            tickvals: yIndex,
            ticktext: sorted.map((maturity) => maturity.tenor_label || maturity.label),
          },
          // Pinned, zero-anchored z-axis: the surface stops re-zooming itself across dates.
          zaxis: { title: { text: "implied vol" }, range: [0, SURFACE_Z_AXIS_MAX] },
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

const SMILE_HEAD = "implied vol vs log-moneyness; puts ◄ ATM ► calls";

// Trader-unit ticks: log-moneyness as a plain decimal k (not -3.00e-1), IV as a percent. The old
// ".2e" scientific formatting on both axes was unreadable for an operator (the 2026-06-16 bilan).
const SMILE_LAYOUT = {
  xaxis: { title: { text: "log-moneyness (k)" }, zeroline: true, tickformat: ".2f" },
  yaxis: { title: { text: "implied vol" }, rangemode: "tozero" as const, tickformat: ".0%" },
  legend: { orientation: "h" as const, y: -0.22 },
  hovermode: "closest" as const,
};

// The smile for ONE tenor: the put wing (k ≤ 0, red) and the call wing (k ≥ 0, green) SUPERIMPOSED
// on a shared log-moneyness axis, joining at ATM (k = 0). The two curves read whole — the vertical
// gap between the wings IS the skew (ADR 0048 per-side overlay; the `combined` shape is the union of
// both). Side-agnostic by design: the page no longer has a put/call switch (the asymmetry is the
// point). A tenor the capture didn't reach is handled upstream as a labelled gap; here an empty
// smile is an honest empty state, never a blank.
export function SmileChart({
  maturities,
  maturityLabel,
}: {
  maturities: AnalyticsMaturity[];
  // The selected tenor's label. Falls back to the front tenor when the label isn't found.
  maturityLabel?: string;
}) {
  const sorted = [...maturities].sort((a, b) => a.maturity_years - b.maturity_years);
  if (sorted.length === 0) {
    const label = `Smile — ${SMILE_HEAD}`;
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No smile to plot yet.</p>
      </figure>
    );
  }

  const maturity = sorted.find((m) => m.label === maturityLabel) ?? sorted[0];
  const degenerate = maturity.surface_slice?.degenerate ?? false;
  const clean = cleanSmile(maturity.smile.log_moneyness, maturity.smile.implied_vols);
  const nDropped = clean.nDroppedNonFinite + clean.nDroppedAbsurd + clean.nDroppedDuplicate;
  const dropNote = nDropped > 0 ? ` — ${nDropped} pt${nDropped === 1 ? "" : "s"} flagged` : "";
  const label = `Smile — ${maturity.label} (${SMILE_HEAD})${
    degenerate ? " ⚠ degenerate fit" : ""
  }${dropNote}`;

  const putPairs: Array<[number, number]> = [];
  const callPairs: Array<[number, number]> = [];
  clean.logMoneyness.forEach((k, i) => {
    if (k <= 0) putPairs.push([k, clean.impliedVols[i]]);
    if (k >= 0) callPairs.push([k, clean.impliedVols[i]]);
  });
  putPairs.sort((a, b) => a[0] - b[0]);
  callPairs.sort((a, b) => a[0] - b[0]);
  const wingTrace = (name: string, color: string, pairs: Array<[number, number]>): Data => ({
    type: "scatter",
    mode: "lines+markers",
    name,
    x: pairs.map((p) => p[0]),
    y: pairs.map((p) => p[1]),
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
        <p>No smile points for this tenor.</p>
      </figure>
    );
  }
  return <Plot label={label} height={360} data={traces} layout={SMILE_LAYOUT} />;
}

const GREEKS_SHAPE_HEAD = "raw Greeks vs strike; gamma/vega bell, delta S-curve (where it peaks)";

const GREEKS_SHAPE_LAYOUT = {
  xaxis: { title: { text: "strike" }, tickformat: ".2s" },
  yaxis: { title: { text: "delta (S-curve)" }, zeroline: true, tickformat: ".2f" },
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
}: {
  maturities: AnalyticsMaturity[];
  maturityLabel?: string;
}) {
  const label = `Greek profiles — ${GREEKS_SHAPE_HEAD}`;
  if (maturities.length === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No Greek profiles for this tenor yet.</p>
      </figure>
    );
  }

  const isAll = maturityLabel === ALL_MATURITIES || maturityLabel === undefined;
  const frontMaturity = [...maturities].sort((a, b) => a.maturity_years - b.maturity_years)[0];
  const maturity = isAll
    ? frontMaturity
    : (maturities.find((m) => m.label === maturityLabel) ?? frontMaturity);

  const points: AnalyticsPoint[] = [...maturity.points].sort((a, b) => a.strike - b.strike);
  if (points.length === 0) {
    return (
      <figure aria-label={`${label} — ${maturity.label}`} className="plot">
        <figcaption>
          Greek profiles — {maturity.label} ({GREEKS_SHAPE_HEAD})
        </figcaption>
        <p>No strikes for this tenor.</p>
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
  };

  return (
    <Plot
      label={`Greek profiles — ${maturity.label} (${GREEKS_SHAPE_HEAD})`}
      height={360}
      data={[deltaCurve, gammaCurve, vegaCurve]}
      layout={GREEKS_SHAPE_LAYOUT}
    />
  );
}
