import { asOfClose, type CoverageCounts, coverageHeadline, sciUnit } from "./format";

export type MetricId =
  | "atm_level"
  | "term_structure_slope"
  | "iv_rank"
  | "skew_25d"
  | "rv_minus_iv"
  | "rho_bar"
  | "convexity_25d"
  | "surface"
  | "smile"
  | "greek_profiles"
  | "surface_coverage";

export type ExplainMode = "strict" | "indicative";

export type ExplainSource = "signal" | "projected" | "surface";

export interface ExplainContext {
  underlying?: string | null;
  asOf?: string | null;
  // The BFF-resolved close instant (venue time-of-day + zone, e.g. "17:30 CET") for the active
  // frame — threaded from /api/analytics, never re-derived from a front-side map.
  closeInstant?: string | null;
  mode?: ExplainMode;
  source?: ExplainSource;
  tenorLabel?: string | null;
  value?: number | null;
  unit?: string | null;
  coverage?: CoverageCounts | null;
}

export interface ExplainEntry {
  label: string;
  whatIs: string;
  howToRead: string;
  unit: string | null;
  whereFrom: (ctx: ExplainContext) => string;
}

const NOT_RECORDED = "signal not recorded for this close";

function hasValue(ctx: ExplainContext): boolean {
  return ctx.value !== null && ctx.value !== undefined && Number.isFinite(ctx.value);
}

function tenorClause(ctx: ExplainContext): string {
  return ctx.tenorLabel ? ` · ${ctx.tenorLabel}` : "";
}

function modeClause(ctx: ExplainContext): string {
  if (ctx.mode === "indicative") {
    return " · indicative mark, not the stored close";
  }
  return "";
}

function signalWhereFrom(ctx: ExplainContext): string {
  if (!hasValue(ctx)) return NOT_RECORDED;
  return `recorded signal${tenorClause(ctx)} · ${asOfClose(ctx.asOf, ctx.closeInstant)}${modeClause(ctx)}`;
}

function projectedWhereFrom(ctx: ExplainContext): string {
  if (!hasValue(ctx)) return NOT_RECORDED;
  return `projected from the smile${tenorClause(ctx)} · ${asOfClose(ctx.asOf, ctx.closeInstant)}${modeClause(ctx)}`;
}

function surfaceWhereFrom(ctx: ExplainContext): string {
  const subject = ctx.underlying ?? "-";
  const head = `${subject} · ${asOfClose(ctx.asOf, ctx.closeInstant)}`;
  const modeWord = ctx.mode === "indicative" ? "indicative" : "strict";
  if (ctx.coverage) {
    return `${head} · ${modeWord} · ${coverageHeadline(ctx.coverage)}`;
  }
  return `${head} · ${modeWord} · coverage unavailable`;
}

export const EXPLAIN: Record<MetricId, ExplainEntry> = {
  atm_level: {
    label: "ATM level",
    whatIs: "at-the-money implied vol",
    howToRead: "the spine of the smile, where the at-the-money options are priced",
    unit: "Vol",
    whereFrom: projectedWhereFrom,
  },
  term_structure_slope: {
    label: "Term-structure slope",
    whatIs: "far − near IV",
    howToRead: "< 0 = backwardation = risk imminent; upward slope is the calm norm",
    unit: "Vol",
    whereFrom: signalWhereFrom,
  },
  iv_rank: {
    label: "IV-rank",
    whatIs: "today's IV in its 1-year range",
    howToRead: "0 % = bottom of the year's range, 100 % = top",
    unit: "Vol",
    whereFrom: signalWhereFrom,
  },
  skew_25d: {
    label: "Skew 25Δ",
    whatIs: "risk-reversal: IV(25Δ put) − IV(25Δ call)",
    howToRead: "positive = downside priced richer than upside (the usual equity-index sign)",
    unit: "Vol",
    whereFrom: projectedWhereFrom,
  },
  rv_minus_iv: {
    label: "RV − IV",
    whatIs: "realized − implied",
    howToRead: "> 0 = vol cheap (buy); < 0 = vol rich (sell)",
    unit: "Vol",
    whereFrom: signalWhereFrom,
  },
  rho_bar: {
    label: "ρ̄",
    whatIs: "implied correlation across the members (hybrid read)",
    howToRead: "−1..+1; high ρ̄ = members move together, dispersion thin",
    unit: "(ratio)",
    whereFrom: signalWhereFrom,
  },
  convexity_25d: {
    label: "Convexity 25Δ",
    whatIs: "butterfly: IV(25Δp) + IV(25Δc) − 2·ATM",
    howToRead: "positive = wings bid above ATM (smile curvature); vp = vol point = 0.01 IV",
    unit: "Vol",
    whereFrom: projectedWhereFrom,
  },
  surface: {
    label: "Volatility surface",
    whatIs: "implied-volatility surface (vol vs log-moneyness vs maturity)",
    howToRead: "the ATM ridge is the spine; the tilt across maturities is the term structure",
    unit: "Vol",
    whereFrom: surfaceWhereFrom,
  },
  smile: {
    label: "Smile",
    whatIs: "implied vol vs log-moneyness; puts ◄ ATM ► calls",
    howToRead: "the vertical gap between the put and call wings is the skew",
    unit: "Vol",
    whereFrom: surfaceWhereFrom,
  },
  greek_profiles: {
    label: "Greek profiles",
    whatIs: "raw Greeks vs strike; gamma/vega bell, delta S-curve (where it peaks)",
    howToRead: "the bell peaks at ATM; the delta S-curve crosses 0.5 near ATM",
    unit: null,
    whereFrom: surfaceWhereFrom,
  },
  surface_coverage: {
    label: "Surface coverage",
    whatIs: "what fraction of the captured chain the surface actually rests on",
    howToRead: "two-sided = quotes with both bid and ask; one-sided excluded = dropped from strict",
    unit: null,
    whereFrom: surfaceWhereFrom,
  },
};

export const METRIC_IDS = Object.keys(EXPLAIN) as MetricId[];

export function isMetricId(id: string): id is MetricId {
  return Object.prototype.hasOwnProperty.call(EXPLAIN, id);
}

export class UnknownMetricError extends Error {
  constructor(public readonly id: string) {
    super(`unknown metric: ${id}`);
    this.name = "UnknownMetricError";
  }
}

export interface ExplainedMetric {
  id: MetricId;
  label: string;
  whatIs: string;
  howToRead: string;
  whereFrom: string;
  value: string | null;
}

// Non-throwing entry lookup for UI consumers that only need the static copy (the ⓘ tooltip and the
// assistant panel's citation/"what is this" labels). Returns null on an id outside the closed
// vocabulary — the same guard as the throwing seam, surfaced as a typed null rather than free text.
export function explainEntry(id: string): ExplainEntry | null {
  return isMetricId(id) ? EXPLAIN[id] : null;
}

export function explainWithContext(id: string, ctx: ExplainContext = {}): ExplainedMetric {
  if (!isMetricId(id)) throw new UnknownMetricError(id);
  const entry = EXPLAIN[id];
  const hasNumber = ctx.value !== null && ctx.value !== undefined && Number.isFinite(ctx.value);
  return {
    id,
    label: entry.label,
    whatIs: entry.whatIs,
    howToRead: entry.howToRead,
    whereFrom: entry.whereFrom(ctx),
    value: hasNumber ? sciUnit(ctx.value, ctx.unit ?? entry.unit) : null,
  };
}
