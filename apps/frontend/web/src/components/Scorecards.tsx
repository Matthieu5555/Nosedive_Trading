import type { AnalyticsMaturity, Signal } from "../api";
import { computeScorecards } from "../lib/scorecards";
import { tourAnchor } from "../lib/tour";
import { InfoDot } from "./InfoDot";

// "as of 2026-06-17 17:30 CET (close)" — the date *and* the close instant, never the bare date that
// can't tell a PM which instant the surface rests on. The instant is the BFF-resolved value (venue
// time-of-day + zone from the index registry, OESX settlement 17:30 — NOT the 22:00 XEUR futures
// close), threaded in — never a front-side constant. Absent instant → date only (never a guess).
export function asOfCloseLine(
  asOf: string | null | undefined,
  closeInstant?: string | null,
): string | null {
  if (!asOf) return null;
  return closeInstant ? `as of ${asOf} ${closeInstant} (close)` : `as of ${asOf}`;
}

// One vol-point figure (a difference of two implied vols) in trader units: vol points = IV × 100,
// signed, one decimal. "+1.8 vp" / "−0.4 vp". A null reads "—" (the honest gap).
function volPoints(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "-";
  const vp = value * 100;
  const sign = vp > 0 ? "+" : "";
  return `${sign}${vp.toFixed(1)} vp`;
}

// An absolute level (ATM IV, IV-rank, correlation) as a percent, one decimal: "18.4%". Null "—".
function levelPercent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

// The sign-color law (cockpit-ux D): a signed instant-read number reads green when positive, coral
// when negative, neutral when zero/absent — the same grammar every signed number on the page obeys.
// `null` (no value) returns no class so a missing read stays muted, never miscoloured.
type SignColor = "positive" | "negative" | null;
export function signColor(value: number | null | undefined): SignColor {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return null;
  return value > 0 ? "positive" : "negative";
}

interface CardSpec {
  label: string;
  value: string;
  // A short always-on subtitle (the tenor / the one-glance read). The longer explanation lives in
  // `info`, behind the hover ⓘ, so the card stays the big-number + short-label read it should be.
  hint: string;
  // The verbose "what is this / how to read it" sentence, relocated off the card face into the
  // hover ⓘ so the grid declutters without losing the information.
  info: string;
  // The signed scalar the value is read from, used to pick the sign colour. Absent/null = no colour
  // (a pure level like ATM or IV-rank is not signed, so it stays neutral).
  sign?: number | null;
}

// The headline six-number instant read (cockpit-ux B, amends the locked ⓪): the cross-cutting book
// state, not the smile read (convexity is demoted to the smile block). Order:
//   ATM level · Term-structure slope · IV-rank · Skew 25Δ · RV−IV · ρ̄
// ATM/skew come from the projected smile/delta grid at the reference tenor (3m, else nearest); the
// slope / IV-rank / RV−IV / ρ̄ are the persisted signals (the BFF computed them — we never recompute
// a signal here). A metric with no data shows "—"; signed reads carry the sign colour.
export function Scorecards({
  maturities,
  ivVsRealized,
  termStructureSlope,
  ivRank,
  impliedCorrelation,
  underlying = null,
  closeInstant = null,
  asOf = null,
  runId = null,
}: {
  maturities: AnalyticsMaturity[];
  // The persisted signals for the index, or null when the signal layer hasn't recorded one for this
  // close. Each carries its own value + unit; we never re-derive the math.
  ivVsRealized: Signal | null;
  termStructureSlope: Signal | null;
  ivRank: Signal | null;
  impliedCorrelation: Signal | null;
  // Provenance of the band (Principle 2 — "where did this number come from?"). All optional so the
  // band still renders before the page threads them: the index symbol labels the band, the
  // BFF-resolved close instant says which instant the surface rests on, the resolved as-of date
  // stamps which close these numbers stand on, the run_id names the capture. Absent close instant →
  // the as-of line degrades to a date only, never printed wrong.
  underlying?: string | null;
  closeInstant?: string | null;
  asOf?: string | null;
  runId?: string | null;
}) {
  const card = computeScorecards(maturities);
  const asOfLine = asOfCloseLine(asOf, closeInstant);
  // Where the numbers came from, in PM register: ATM/Skew are projected off the captured surface;
  // slope/IV-rank/RV−IV/ρ̄ are persisted signals the BFF computed (we never recompute one here). The
  // run_id names the exact capture so a PM can defend any number against any question.
  const provenanceBody = `ATM & Skew are read off the captured volatility surface; Term-structure slope, IV-rank, RV−IV and average correlation are persisted signals computed by the backend (never recomputed on the front).${
    runId ? ` Source capture: run ${runId}.` : ""
  }`;
  const tenorNote = card
    ? card.isReferenceTenor
      ? `at ${card.tenorLabel}`
      : `at ${card.tenorLabel} (3m not captured)`
    : "no surface";

  const cards: CardSpec[] = [
    {
      label: "ATM level",
      value: levelPercent(card?.atm ?? null),
      hint: tenorNote,
      info: `At-the-money implied volatility, read off the captured surface ${tenorNote}.`,
    },
    {
      // Term-structure slope: longer-dated IV − shorter-dated, in vol points. Positive (upward) is
      // the calm norm; negative (backwardation) flags near-term stress — §4.2 "signal fort".
      label: "Term-structure slope",
      value: termStructureSlope ? volPoints(termStructureSlope.value) : "-",
      hint: termStructureSlope ? termStructureSlope.tenor_label : "signal not recorded",
      info: termStructureSlope
        ? `Far minus near implied vol, in vol points (${termStructureSlope.tenor_label}). Below zero is backwardation, a sign of near-term stress.`
        : "Far minus near implied vol. No signal recorded for this close.",
      sign: termStructureSlope?.value ?? null,
    },
    {
      // IV-rank: where today's IV sits in its 1-year range, 0–100%. A pure level (not signed).
      label: "IV-rank",
      value: ivRank ? levelPercent(ivRank.value) : "-",
      hint: ivRank ? ivRank.tenor_label : "signal not recorded",
      info: ivRank
        ? `Where today's implied vol sits inside its 1-year range, from 0% (year low) to 100% (year high). Tenor ${ivRank.tenor_label}.`
        : "Where today's implied vol sits in its 1-year range. No signal recorded for this close.",
    },
    {
      label: "Skew 25Δ",
      value: volPoints(card?.skew ?? null),
      hint: tenorNote,
      info: `How much more the market is paying to protect against a fall than to bet on a rise, in vol points (${tenorNote}). A positive number means downside protection is the dearer side, the usual sign of nervousness.`,
      sign: card?.skew ?? null,
    },
    {
      // RV−IV: positive means the market moved more than options priced (vol cheap → buy). Read
      // straight off the persisted signal; the unit string travels with it from the BFF.
      label: "RV - IV",
      value: ivVsRealized ? volPoints(ivVsRealized.value) : "-",
      hint: ivVsRealized ? ivVsRealized.tenor_label : "signal not recorded",
      info: ivVsRealized
        ? `How much the market has actually been moving versus how much option prices expect it to move, in vol points (${ivVsRealized.tenor_label}). Above zero means it has been moving more than options are pricing, so options look cheap to buy.`
        : "How much the market has actually been moving versus how much option prices expect. No signal recorded for this close.",
      sign: ivVsRealized?.value ?? null,
    },
    {
      // Average implied correlation across the members, −1..+1. The dispersion book's thesis
      // (TARGET §3 S1 / R3). Today a hybrid implied-index / realized-constituent read — labelled
      // honestly until constituent IVs land. Plain-words label avoids the rho-bar glyph that
      // rendered mispositioned in the numeric font.
      label: "Avg correlation (ρ)",
      value: impliedCorrelation ? levelPercent(impliedCorrelation.value) : "-",
      hint: impliedCorrelation ? impliedCorrelation.tenor_label : "signal not recorded",
      info: impliedCorrelation
        ? `Average implied correlation across the index members (${impliedCorrelation.tenor_label}), today a hybrid implied-index and realized-constituent read. Higher means members move more in lockstep.`
        : "Average implied correlation across the index members. No signal recorded for this close.",
    },
  ];

  return (
    <section
      className="scorecards-band"
      aria-label="Volatility scorecards"
      {...tourAnchor(
        "market.scorecard",
        "Indicator scorecards",
        "The headline indicators at a glance: how rich vol is, the term-structure slope, and more.",
      )}
    >
      {(asOfLine || underlying) && (
        <p className="scorecards-legend" aria-label="Scorecard provenance">
          {underlying ? <strong>{underlying}</strong> : null}
          {underlying && asOfLine ? " · " : null}
          {asOfLine}
          <InfoDot label="Scorecards, where these numbers come from" body={provenanceBody} />
        </p>
      )}
      <div className="scorecards">
        {cards.map((c) => {
          const color = c.sign !== undefined ? signColor(c.sign) : null;
          return (
            <article key={c.label} className="scorecard" aria-label={c.label}>
              <p className="scorecard__label">
                <span>{c.label}</span>
                <InfoDot label={`${c.label}, what this is`} body={c.info} />
              </p>
              <p className={`scorecard__value${color ? ` ${color}` : ""}`}>{c.value}</p>
              <p className="scorecard__hint">{c.hint}</p>
            </article>
          );
        })}
      </div>
      <p className="scorecards-legend" aria-label="Sign legend">
        Read the signs:{" "}
        <span className="positive">RV - IV above zero means options look cheap to buy</span>;{" "}
        <span className="negative">RV - IV below zero means options look expensive to sell</span>;{" "}
        <span className="negative">
          a negative term-structure slope means near-term risk is rising
        </span>
        . A vol point (vp) is one hundredth of an annualized volatility, so a move from 18% to 19%
        is one vol point.
      </p>
    </section>
  );
}
