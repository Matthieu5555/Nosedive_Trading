import type { Data } from "plotly.js";

import {
  ALL_MATURITIES,
  type AnalyticsMaturity,
  type AnalyticsPoint,
  type OptionSide,
  type PriceHistoryResponse,
  type SurfaceDense,
} from "../api";
import { sci, withCurrency } from "../lib/format";
import {
  cleanDenseSurface,
  cleanSmile,
  flaggedNote,
  isSaneIv,
  IV_SANE_MAX,
} from "../lib/volRobust";
import { CandleChart } from "./CandleChart";
import { CHART_COLORS, VOL_COLORSCALE } from "./chartTheme";
import { LightweightLineChart, type LightweightLineSeries } from "./LightweightLineChart";
import { Plot } from "./Plot";

const SURFACE_Z_MAX = IV_SANE_MAX;

// The persistent put/call switch filters every surface/smile/Greeks panel to one wing. Puts are
// the downside (log-moneyness ≤ 0), calls the upside (≥ 0); ATM (k = 0) is shared, so it survives
// either filter and the two wings always join at the money. `undefined` keeps both wings (the
// accordion's full-smile view still wants that).
function keepK(k: number, side?: OptionSide): boolean {
  if (side === "put") return k <= 0;
  if (side === "call") return k >= 0;
  return true;
}

// Same split applied to a signed target delta (puts ≤ 0, calls ≥ 0), for the Greeks views whose
// rows/bands are keyed by delta rather than log-moneyness. ATM bands (target delta 0) survive both.
function keepDelta(targetDelta: number, side?: OptionSide): boolean {
  if (side === "put") return targetDelta <= 0;
  if (side === "call") return targetDelta >= 0;
  return true;
}

const SIDE_NOTE: Record<OptionSide, string> = { put: "puts", call: "calls" };
function sideSuffix(side?: OptionSide): string {
  return side ? ` — ${SIDE_NOTE[side]}` : "";
}

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
function DenseVolSurface({ surface, side }: { surface: SurfaceDense; side?: OptionSide }) {
  // Robustness (render layer only — the served values are never mutated): a railed slice serves
  // absurd IVs (108%, 140% at deep-OTM deltas) and duplicate log-moneyness columns; left raw they
  // spike the nappe's height and stretch its colour band. Clamp out-of-band / non-finite cells to
  // null holes (bridged by connectgaps) and collapse duplicate-k columns, then surface an honest
  // count of the flagged slices instead of rendering the garbage peak.
  const full = cleanDenseSurface(
    surface.log_moneyness,
    surface.maturity_years,
    surface.implied_vol,
  );
  // The put/call switch keeps one wing of columns (k ≤ 0 or k ≥ 0), ATM shared. Slicing the
  // columns rather than the served grid keeps every maturity row intact.
  const keepCols = full.logMoneyness.map((k, j) => (keepK(k, side) ? j : -1)).filter((j) => j >= 0);
  const cleaned = {
    logMoneyness: keepCols.map((j) => full.logMoneyness[j]),
    maturityYears: full.maturityYears,
    impliedVol: full.impliedVol.map((row) => keepCols.map((j) => row[j])),
    nFlaggedSlices: full.nFlaggedSlices,
  };
  const note = flaggedNote(cleaned.nFlaggedSlices, "slice");
  const trace = {
    type: "surface",
    x: cleaned.logMoneyness,
    y: cleaned.maturityYears,
    z: cleaned.impliedVol,
    name: "IV surface",
    colorscale: VOL_COLORSCALE,
    cmin: 0,
    cmax: SURFACE_Z_MAX,
    connectgaps: true,
    colorbar: { title: { text: "IV" } },
  } as Data;
  const label = `${SURFACE_LABEL}${sideSuffix(side)}`;
  return (
    <Plot
      label={note ? `${label} — ⚠ ${note}` : label}
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
  side,
}: {
  surface?: SurfaceDense | null;
  maturities: AnalyticsMaturity[];
  side?: OptionSide;
}) {
  // Render the smooth reconstructed surface whenever the fit produced one; otherwise fall back to
  // the coarse grid built from the sparse delta-band points below (e.g. a single fitted slice, or
  // the surface-grid fallback with no fit).
  if (surface && surface.maturity_years.length > 0 && surface.log_moneyness.length > 0) {
    return <DenseVolSurface surface={surface} side={side} />;
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
    .map((maturity) => {
      const clean = cleanSmile(maturity.smile.log_moneyness, maturity.smile.implied_vols);
      // Keep only the selected wing's columns (ATM shared) so the fallback surface matches the
      // dense path under the put/call switch.
      const keep = clean.logMoneyness
        .map((k, i) => (keepK(k, side) ? i : -1))
        .filter((i) => i >= 0);
      return {
        maturity,
        clean: {
          ...clean,
          logMoneyness: keep.map((i) => clean.logMoneyness[i]),
          impliedVols: keep.map((i) => clean.impliedVols[i]),
        },
      };
    })
    .filter(({ clean }) => clean.logMoneyness.length > 0)
    .sort((a, b) => a.maturity.maturity_years - b.maturity.maturity_years);
  const nFlaggedSlices = cleaned.filter(
    ({ clean }) => clean.nDroppedAbsurd + clean.nDroppedNonFinite > 0,
  ).length;
  const note = flaggedNote(nFlaggedSlices, "slice");
  const baseLabel = `${SURFACE_LABEL}${sideSuffix(side)}`;
  const label = note ? `${baseLabel} — ⚠ ${note}` : baseLabel;
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

const HEATMAP_LABEL = "Implied-volatility nappe (heatmap: IV over log-moneyness × maturity)";

// The nappe as a flat heatmap, meant to sit stacked with the 3D surface (§3.4). It plots the SAME
// dense (maturity × log-moneyness) lattice, the SAME Plasma colourscale, and — crucially — the
// SAME pinned colour band (zmin/zmax = [0, SURFACE_Z_MAX]) as the 3D nappe, so a colour reads as
// the same IV in both panels and stays stable across trade dates (the CDC's "shared value→colour
// scale"). Render-layer robustness mirrors DenseVolSurface: a railed/non-finite cell is clamped to
// a null hole and the flagged-slice count rides the label, never plotted as a colour spike. The
// served values are untouched.
export function VolHeatmap({ surface }: { surface?: SurfaceDense | null }) {
  if (!surface || surface.maturity_years.length === 0 || surface.log_moneyness.length === 0) {
    return (
      <figure aria-label={HEATMAP_LABEL} className="plot">
        <figcaption>{HEATMAP_LABEL}</figcaption>
        <p>No reconstructed surface to plot yet.</p>
      </figure>
    );
  }
  const cleaned = cleanDenseSurface(
    surface.log_moneyness,
    surface.maturity_years,
    surface.implied_vol,
  );
  const note = flaggedNote(cleaned.nFlaggedSlices, "slice");
  const trace = {
    type: "heatmap",
    x: cleaned.logMoneyness,
    y: cleaned.maturityYears,
    z: cleaned.impliedVol,
    name: "IV nappe",
    colorscale: VOL_COLORSCALE,
    zmin: 0,
    zmax: SURFACE_Z_MAX,
    connectgaps: true,
    colorbar: { title: { text: "IV" } },
  } as Data;
  return (
    <Plot
      label={note ? `${HEATMAP_LABEL} — ⚠ ${note}` : HEATMAP_LABEL}
      height={360}
      data={[trace]}
      layout={{
        xaxis: { title: { text: "log-moneyness" } },
        yaxis: { title: { text: "maturity (years)" } },
      }}
    />
  );
}

const ATM_TERM_LABEL = "ATM term structure (at-the-money implied vol vs maturity)";

// A compact maturity label for a point sourced from the dense lattice (which carries years, not a
// tenor label): months under a year, else years to 2 dp. Smile-sourced points keep their tenor.
function maturityYearsLabel(years: number): string {
  if (years < 1) return `${Math.max(1, Math.round(years * 12))}m`;
  return `${Number(years.toFixed(2))}y`;
}

interface AtmPoint {
  months: number;
  iv: number;
  label: string;
}

// ATM cut of the dense nappe: the column nearest log-moneyness 0 is at-the-money for every
// maturity row. Drives off the reconstructed lattice (the same grid the heatmap/3D use), which
// carries more maturities than the sparse band points. Out-of-band/non-finite ATM cells are
// excluded (cleanDenseSurface) and counted as flagged.
function atmTermFromDense(surface: SurfaceDense): { points: AtmPoint[]; nFlagged: number } {
  const cleaned = cleanDenseSurface(
    surface.log_moneyness,
    surface.maturity_years,
    surface.implied_vol,
  );
  if (cleaned.logMoneyness.length === 0) return { points: [], nFlagged: cleaned.nFlaggedSlices };
  let atmCol = 0;
  cleaned.logMoneyness.forEach((k, j) => {
    if (Math.abs(k) < Math.abs(cleaned.logMoneyness[atmCol])) atmCol = j;
  });
  const points: AtmPoint[] = [];
  cleaned.maturityYears.forEach((years, i) => {
    const iv = cleaned.impliedVol[i]?.[atmCol];
    if (isSaneIv(iv)) {
      points.push({
        months: Math.max(1, Math.round(years * 12)),
        iv,
        label: maturityYearsLabel(years),
      });
    }
  });
  return { points, nFlagged: cleaned.nFlaggedSlices };
}

// Fallback ATM term structure when no dense surface was fitted: each maturity's own smile, its
// point nearest log-moneyness 0. A maturity whose smile cleans to nothing is counted as flagged.
function atmTermFromSmiles(maturities: AnalyticsMaturity[]): {
  points: AtmPoint[];
  nFlagged: number;
} {
  let nFlagged = 0;
  const points: AtmPoint[] = [];
  for (const maturity of maturities) {
    const clean = cleanSmile(maturity.smile.log_moneyness, maturity.smile.implied_vols);
    if (clean.logMoneyness.length === 0) {
      nFlagged += 1;
      continue;
    }
    let atm = 0;
    clean.logMoneyness.forEach((k, i) => {
      if (Math.abs(k) < Math.abs(clean.logMoneyness[atm])) atm = i;
    });
    points.push({
      months: Math.max(1, Math.round(maturity.maturity_years * 12)),
      iv: clean.impliedVols[atm],
      label: maturity.tenor_label || maturity.label,
    });
  }
  return { points, nFlagged };
}

// The §3.5 2D cut beside the smile: at-the-money IV vs maturity, read off the dense nappe when
// present (more tenors, smooth fit), else off the per-maturity smiles. Drawn on the numeric-x
// yield-curve line panel (x = maturity in months; the shared formatter renders 3m / 1y); IV is an
// analytics quantity, so scientific notation. Honest empty state, never a blank.
export function AtmTermStructure({
  surface,
  maturities,
}: {
  surface?: SurfaceDense | null;
  maturities: AnalyticsMaturity[];
}) {
  const hasDense =
    surface != null && surface.maturity_years.length > 0 && surface.log_moneyness.length > 0;
  const { points, nFlagged } = hasDense ? atmTermFromDense(surface) : atmTermFromSmiles(maturities);
  const note = flaggedNote(nFlagged, "slice");
  const label = note ? `${ATM_TERM_LABEL} — ⚠ ${note}` : ATM_TERM_LABEL;
  if (points.length === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No ATM term structure to plot yet.</p>
      </figure>
    );
  }
  const series: LightweightLineSeries = {
    label: "ATM IV",
    color: CHART_COLORS.positive,
    points: points.map((p) => ({ x: p.months, label: p.label, value: p.iv })),
  };
  return (
    <LightweightLineChart
      label={label}
      series={[series]}
      yUnit="IV"
      valueFormatter={(value) => sci(value)}
    />
  );
}

// Smile wing colours: puts (downside) read red, calls (upside) green — the convention an
// operator expects, read off the shared --negative / --positive design tokens.
const PUT_COLOR = CHART_COLORS.negative;
const CALL_COLOR = CHART_COLORS.positive;

// The smile is a Plotly scatter on a REAL log-moneyness axis — it can go negative natively, so ATM
// sits at 0, OTM puts on the left (k < 0), OTM calls on the right (k > 0), and the downward
// left→right slope IS the skew. (It used to be coerced onto a yield-curve panel, whose axis rejects
// negatives; the +1×1000 shift that worked around it leaked through as nonsense month ticks like
// "71Y7M" — the real axis removes the hack entirely.)
const SMILE_HEAD = "implied vol vs log-moneyness; puts ◄ ATM ► calls";

// Tenor ramp for the all-maturities overlay: near tenors read cool (blue), far tenors warm (amber),
// so the stack of smiles carries the term structure as depth. Sampled along the same lerp as the
// delta-band ramp below.
const TENOR_RAMP = ["#86b9ff", "#e5c36a"] as const;
function tenorColor(index: number, count: number): string {
  if (count <= 1) return TENOR_RAMP[0];
  return lerpHex(TENOR_RAMP[0], TENOR_RAMP[1], index / (count - 1));
}

const SMILE_LAYOUT = {
  xaxis: { title: { text: "log-moneyness" }, zeroline: true, tickformat: ".2e" },
  yaxis: { title: { text: "implied vol" }, rangemode: "tozero" as const, tickformat: ".2e" },
  legend: { orientation: "h" as const, y: -0.22 },
  hovermode: "closest" as const,
};

// One side-filtered wing of a cleaned smile as plottable (k, IV) arrays, sorted by k.
function smileWing(maturity: AnalyticsMaturity, side?: OptionSide): { ks: number[]; vols: number[] } {
  const clean = cleanSmile(maturity.smile.log_moneyness, maturity.smile.implied_vols);
  const pairs: Array<[number, number]> = [];
  clean.logMoneyness.forEach((k, i) => {
    if (keepK(k, side)) pairs.push([k, clean.impliedVols[i]]);
  });
  pairs.sort((a, b) => a[0] - b[0]);
  return { ks: pairs.map((p) => p[0]), vols: pairs.map((p) => p[1]) };
}

export function SmileChart({
  maturities,
  maturityLabel,
  side,
}: {
  maturities: AnalyticsMaturity[];
  // A specific tenor label, or "All maturities" (the default) to overlay every captured tenor.
  maturityLabel?: string;
  side?: OptionSide;
}) {
  const sorted = [...maturities].sort((a, b) => a.maturity_years - b.maturity_years);
  const isAll = maturityLabel === ALL_MATURITIES || maturityLabel === undefined;

  if (sorted.length === 0) {
    const label = `Smile — ${SMILE_HEAD}${sideSuffix(side)}`;
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No smile to plot yet.</p>
      </figure>
    );
  }

  if (isAll) {
    // Overlay every tenor's smile — the surface read as a 2D stack of slices, coloured near→far.
    const traces: Data[] = sorted
      .map((maturity, i): Data | null => {
        const { ks, vols } = smileWing(maturity, side);
        if (ks.length === 0) return null;
        return {
          type: "scatter",
          mode: "lines",
          name: maturity.tenor_label || maturity.label,
          x: ks,
          y: vols,
          line: { color: tenorColor(i, sorted.length), width: 1.5 },
        };
      })
      .filter((t): t is Data => t !== null);
    // A degenerate calibration must stay visible even in the overlay — count the flagged tenors and
    // ride the count on the label rather than silently drawing them as clean slices.
    const nDegen = sorted.filter((m) => m.surface_slice?.degenerate).length;
    const degenNote = nDegen > 0 ? ` ⚠ ${nDegen} degenerate fit${nDegen === 1 ? "" : "s"}` : "";
    const label = `Smile — all maturities (${SMILE_HEAD})${degenNote}${sideSuffix(side)}`;
    if (traces.length === 0) {
      return (
        <figure aria-label={label} className="plot">
          <figcaption>{label}</figcaption>
          <p>No smile to plot yet.</p>
        </figure>
      );
    }
    return <Plot label={label} height={360} data={traces} layout={SMILE_LAYOUT} />;
  }

  // One tenor: the put wing (k ≤ 0) red and the call wing (k ≥ 0) green, sharing the ATM point so
  // they join; the put/call switch leaves only the chosen wing.
  const maturity = sorted.find((m) => m.label === maturityLabel) ?? sorted[0];
  const degenerate = maturity.surface_slice?.degenerate ?? false;
  const clean = cleanSmile(maturity.smile.log_moneyness, maturity.smile.implied_vols);
  const nDropped = clean.nDroppedNonFinite + clean.nDroppedAbsurd + clean.nDroppedDuplicate;
  const dropNote = nDropped > 0 ? ` — ${nDropped} pt${nDropped === 1 ? "" : "s"} flagged` : "";
  const label = `Smile — ${maturity.label} (${SMILE_HEAD})${
    degenerate ? " ⚠ degenerate fit" : ""
  }${dropNote}${sideSuffix(side)}`;

  const putPairs: Array<[number, number]> = [];
  const callPairs: Array<[number, number]> = [];
  clean.logMoneyness.forEach((k, i) => {
    if (!keepK(k, side)) return;
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
        <p>No smile points in this wing.</p>
      </figure>
    );
  }
  return <Plot label={label} height={360} data={traces} layout={SMILE_LAYOUT} />;
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

// Delta bands run put → ATM → call (orderedBands sorts by signed target delta). Colour them on a
// continuous put→call diverging ramp (red puts → amber ATM → green calls) rather than a fixed
// palette cycled with `index % n`: a real capture carries ~30 bands, so the old 6-colour cycle
// repeated every sixth line and neither the curves nor the legend swatches could be told apart.
// The ramp encodes each band's place in the smile — the same order the legend reads in.
const BAND_RAMP = ["#ef9c92", "#e5c36a", "#a8e6ba"] as const; // red put → amber ATM → green call

function lerpHex(a: string, b: string, t: number): string {
  const channels = [1, 3, 5].map((i) => {
    const from = parseInt(a.slice(i, i + 2), 16);
    const to = parseInt(b.slice(i, i + 2), 16);
    return Math.round(from + (to - from) * t)
      .toString(16)
      .padStart(2, "0");
  });
  return `#${channels.join("")}`;
}

// The colour for band `index` of `count` ordered bands, sampled along the put→call ramp.
function bandColor(index: number, count: number): string {
  if (count <= 1) return BAND_RAMP[1];
  const t = (index / (count - 1)) * (BAND_RAMP.length - 1); // 0 = first put … last = last call
  const lo = Math.min(BAND_RAMP.length - 2, Math.floor(t));
  return lerpHex(BAND_RAMP[lo], BAND_RAMP[lo + 1], t - lo);
}

// Distinct delta bands across all maturities, ordered by their signed target delta (put → call)
// so the legend reads left-to-right the way the smile does. Under the put/call switch, only the
// matching wing's bands are kept (ATM, target delta 0, survives both).
function orderedBands(maturities: AnalyticsMaturity[], side?: OptionSide): string[] {
  const target = new Map<string, number>();
  for (const m of maturities) {
    for (const p of m.points) {
      if (!target.has(p.delta_band)) target.set(p.delta_band, p.target_delta);
    }
  }
  return [...target.entries()]
    .filter(([, t]) => keepDelta(t, side))
    .sort((a, b) => a[1] - b[1])
    .map(([band]) => band);
}

function maturityMonths(maturity: AnalyticsMaturity): number {
  return Math.max(1, Math.round(maturity.maturity_years * 12));
}

// One line series per band: (maturity in months, dollar value) for the points that carry a
// finite dollar (an older partition can carry a null $; it is skipped, never plotted as 0).
// Robustness (render layer only): a point on a RAILED slice carries an absurd implied vol
// (108%/140%), and its dollar Greeks are outliers that, plotted, spike the whole panel and flatten
// every real line. Such points are excluded from the term structure (the served data is untouched;
// the point is still visible in the per-maturity transpose table, flagged). A non-finite dollar is
// likewise excluded rather than plotted as a spike.
function bandSeries(
  maturities: AnalyticsMaturity[],
  greek: GreekName,
  side?: OptionSide,
): LightweightLineSeries[] {
  const bands = orderedBands(maturities, side);
  return bands
    .map((band, index): LightweightLineSeries => {
      const points = maturities.flatMap((maturity) => {
        const point = maturity.points.find((p) => p.delta_band === band);
        if (point === undefined) return [];
        // Exclude a point seated on a railed slice (its IV is out of the sane band) — its Greeks
        // are the outliers that spike the panel.
        if (!isSaneIv(point.implied_vol)) return [];
        const dollar = point.metrics[greek].dollar;
        if (dollar === null || dollar === undefined || !Number.isFinite(dollar)) return [];
        return [{ x: maturityMonths(maturity), label: maturity.tenor_label, value: dollar }];
      });
      return { label: band, color: bandColor(index, bands.length), points };
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
  side,
}: {
  maturities: AnalyticsMaturity[];
  currency?: string;
  side?: OptionSide;
}) {
  const label = `Dollar Greeks term structure ($ value vs maturity, by delta band)${sideSuffix(side)}`;
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
      <div className="greeks-term-structure__heading">
        <h3>Dollar Greeks term structure</h3>
        {/* One shared legend for the whole grid: every panel draws one line per delta band on the
            same put→ATM→call ramp, so a single caption with the gradient swatch replaces the tens
            of per-band chips that used to repeat under each of the four panels. */}
        <p className="band-ramp-key">
          <span className="band-ramp-swatch" aria-hidden="true" />
          delta bands: <span className="band-ramp-put">puts</span> →{" "}
          <span className="band-ramp-atm">ATM</span> → <span className="band-ramp-call">calls</span>
        </p>
      </div>
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
              series={bandSeries(sorted, name, side)}
              yUnit={unit}
              showSeriesLegend={false}
            />
          );
        })}
      </div>
    </section>
  );
}
