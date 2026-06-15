// The Tab-1 chart panels. The daily candlestick, the Greek term-structure line charts, and the
// smile use TradingView Lightweight Charts; only the 3D IV surface stays on the Plotly wrapper
// (lightweight-charts has no 3D/mesh path).
//
// Each panel is self-labelling (answers "what am I looking at?") and reads a typed BFF response.
// The 3D IV surface is a Plotly `surface` over (log-moneyness, maturity, implied_vol); the smile
// is a 2D line chart of vol vs log-moneyness, split into a put wing (k ≤ 0) and call wing (k ≥ 0).

import type { Data } from "plotly.js";

import type { AnalyticsMaturity, AnalyticsPoint, PriceHistoryResponse, SurfaceDense } from "../api";
import { sci, withCurrency } from "../lib/format";
import { CandleChart } from "./CandleChart";
import { CHART_COLORS, VOL_COLORSCALE } from "./chartTheme";
import { LightweightLineChart, type LightweightLineSeries } from "./LightweightLineChart";
import { Plot } from "./Plot";

// The z-axis is pinned to [0, SURFACE_Z_MAX] (anchored at 0), not auto-ranged, so the surface
// reads at a stable height — and, via cmin/cmax, a stable colour — across trade dates instead of
// re-zooming on every capture. 35% IV comfortably covers the index-option body.
const SURFACE_Z_MAX = 0.35;

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
  const trace = {
    type: "surface",
    x: surface.log_moneyness,
    y: surface.maturity_years,
    z: surface.implied_vol,
    name: "IV surface",
    colorscale: VOL_COLORSCALE,
    cmin: 0,
    cmax: SURFACE_Z_MAX,
    colorbar: { title: { text: "IV" } },
  } as Data;
  return (
    <Plot
      label={SURFACE_LABEL}
      height={480}
      data={[trace]}
      layout={{
        scene: {
          xaxis: { title: { text: "log-moneyness" } },
          yaxis: { title: { text: "maturity (years)" } },
          zaxis: { title: { text: "implied vol" }, range: [0, SURFACE_Z_MAX] },
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
  const sorted = [...maturities]
    .filter((maturity) => maturity.smile.log_moneyness.length > 0)
    .sort((a, b) => a.maturity_years - b.maturity_years);
  const label = SURFACE_LABEL;
  if (sorted.length === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No surface to plot yet.</p>
      </figure>
    );
  }
  // Common x grid (union of every maturity's log-moneyness axis), so the z-grid stays rectangular
  // even where a coarse long-dated tenor lacks the wing bands.
  const xGrid = [...new Set(sorted.flatMap((m) => m.smile.log_moneyness))].sort((a, b) => a - b);
  const z: (number | null)[][] = sorted.map((maturity) => {
    const byK = new Map(maturity.smile.log_moneyness.map((k, i) => [k, maturity.smile.implied_vols[i]]));
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
    cmax: SURFACE_Z_MAX,
    connectgaps: true,
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
          zaxis: { title: { text: "implied vol" }, range: [0, SURFACE_Z_MAX] },
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

// Smile wing colours: puts (downside) read red, calls (upside) green — the convention an
// operator expects, read off the shared --negative / --positive design tokens.
const PUT_COLOR = CHART_COLORS.negative;
const CALL_COLOR = CHART_COLORS.positive;

// The smile is drawn in TradingView Lightweight Charts (the numeric-x yield-curve panel), not
// Plotly: it renders with a real height like the candlestick / term-structure panels, and the
// x-axis is log-moneyness so the curve reads as a true smile — ATM at 0, OTM puts on the left
// (strikes below the forward, k < 0), OTM calls on the right (k > 0). The downward left→right
// slope IS the skew. Two series — a red put wing (k ≤ 0) and a green call wing (k ≥ 0), sharing
// the ATM point so they join. The yield-curve scale rejects negative x, so log-moneyness is
// shifted +1.0 and scaled ×1000 into the integer axis; the tick formatter restores the real k.
const SMILE_X_OFFSET = 1.0;
const SMILE_X_SCALE = 1000;
const toSmileX = (x: number): number => Math.round((x + SMILE_X_OFFSET) * SMILE_X_SCALE);
// The x tick is the real log-moneyness k, an analytics quantity: scientific notation (the unit
// rides the chart label, which already names log-moneyness).
const fromSmileX = (x: number): string => sci(x / SMILE_X_SCALE - SMILE_X_OFFSET);

export function SmileChart({ maturity }: { maturity: AnalyticsMaturity }) {
  // A degenerate calibration (parameter railed to a bound, non-converged, or arb-breached)
  // is shown flagged, never as a clean fit — the flag clears once real term structure lands.
  const degenerate = maturity.surface_slice?.degenerate ?? false;
  const label = `Smile — ${maturity.label} (implied vol vs log-moneyness; puts ◄ ATM ► calls)${
    degenerate ? " ⚠ degenerate fit" : ""
  }`;
  const ks = maturity.smile.log_moneyness;
  const vols = maturity.smile.implied_vols;
  const puts: LightweightLineSeries = { label: "puts", color: PUT_COLOR, points: [] };
  const calls: LightweightLineSeries = { label: "calls", color: CALL_COLOR, points: [] };
  ks.forEach((k, i) => {
    // The point's x-label is the real log-moneyness (analytics quantity), in scientific notation.
    const point = { x: toSmileX(k), label: sci(k), value: vols[i] };
    if (k <= 0) puts.points.push(point);
    if (k >= 0) calls.points.push(point);
  });
  return (
    <LightweightLineChart
      label={label}
      series={[puts, calls]}
      yUnit="IV"
      xFormatter={fromSmileX}
      // Implied vol is an analytics quantity: scientific notation (the "IV" unit rides yUnit).
      valueFormatter={(value) => sci(value)}
    />
  );
}

// The four dollar-Greeks graphed as a term structure — the curve view of the same
// projected_analytics the DollarGreeks table reads (so it fills and degrades identically:
// empty until the projection grid is populated). One panel per Greek, one line per delta band,
// x = maturity. The $ unit string is read off the payload (ADR 0036), shown as the y-axis title.
type GreekName = keyof AnalyticsPoint["metrics"];

const GREEK_PANELS: ReadonlyArray<{ name: GreekName; title: string }> = [
  { name: "delta", title: "Delta $" },
  { name: "gamma", title: "Gamma $" },
  { name: "vega", title: "Vega $" },
  { name: "theta", title: "Theta $" },
];

const BAND_COLORS = [
  CHART_COLORS.positive,
  "#8fc7ff",
  "#f0cf7a",
  "#d6b3ff",
  CHART_COLORS.negative,
  "#79d7cf",
] as const;

// Distinct delta bands across all maturities, ordered by their signed target delta (put → call)
// so the legend reads left-to-right the way the smile does.
function orderedBands(maturities: AnalyticsMaturity[]): string[] {
  const target = new Map<string, number>();
  for (const m of maturities) {
    for (const p of m.points) {
      if (!target.has(p.delta_band)) target.set(p.delta_band, p.target_delta);
    }
  }
  return [...target.entries()].sort((a, b) => a[1] - b[1]).map(([band]) => band);
}

function maturityMonths(maturity: AnalyticsMaturity): number {
  return Math.max(1, Math.round(maturity.maturity_years * 12));
}

// One line series per band: (maturity in months, dollar value) for the points that carry a
// non-null dollar (an older partition can carry a null $; it is skipped, never plotted as 0).
function bandSeries(maturities: AnalyticsMaturity[], greek: GreekName): LightweightLineSeries[] {
  return orderedBands(maturities)
    .map((band, index): LightweightLineSeries => {
      const points = maturities.flatMap((maturity) => {
        const point = maturity.points.find((p) => p.delta_band === band);
        const dollar = point?.metrics[greek].dollar;
        if (point === undefined || dollar === null || dollar === undefined) return [];
        return [{ x: maturityMonths(maturity), label: maturity.tenor_label, value: dollar }];
      });
      return { label: band, color: BAND_COLORS[index % BAND_COLORS.length], points };
    })
    .filter((series) => series.points.length > 0);
}

// The first non-null unit string for a Greek, to title that panel's y-axis. Falls back to "$".
function unitFor(maturities: AnalyticsMaturity[], greek: GreekName): string {
  for (const m of maturities) {
    for (const p of m.points) {
      const unit = p.metrics[greek].unit;
      if (unit) return unit;
    }
  }
  return "$";
}

export function GreeksTermStructure({
  maturities,
  currency = "$",
}: {
  maturities: AnalyticsMaturity[];
  currency?: string;
}) {
  const label = "Dollar Greeks term structure ($ value vs maturity, by delta band)";
  const sorted = [...maturities].sort((a, b) => a.maturity_years - b.maturity_years);
  if (!sorted.some((m) => m.points.length > 0)) {
    return (
      <section aria-label={label} className="greeks-term-structure">
        <h3>{label}</h3>
        <p>No projected analytics for this ticker/date yet.</p>
      </section>
    );
  }
  return (
    <section aria-label={label} className="greeks-term-structure">
      <h3>{label}</h3>
      <div className="chart-grid">
        {GREEK_PANELS.map(({ name, title }) => {
          // The backend unit carries "$" as the currency placeholder; render it in the index's
          // real quote currency (€ for SX5E) on both the panel label and the y-axis unit.
          // `unitFor` always returns a non-null string, so `withCurrency` does too.
          const unit = withCurrency(unitFor(sorted, name), currency) as string;
          return (
            <LightweightLineChart
              key={name}
              label={`${title} term structure (${unit})`}
              series={bandSeries(sorted, name)}
              yUnit={unit}
            />
          );
        })}
      </div>
    </section>
  );
}
