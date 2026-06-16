import type { AnalyticsMaturity, Signal } from "../api";
import { computeScorecards } from "../lib/scorecards";

// One vol-point figure (a difference of two implied vols) in trader units: vol points = IV × 100,
// signed, one decimal. "+1.8 vp" / "−0.4 vp". A null reads "—" (the honest gap).
function volPoints(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const vp = value * 100;
  const sign = vp > 0 ? "+" : "";
  return `${sign}${vp.toFixed(1)} vp`;
}

// An ATM level (an absolute implied vol) as a percent, one decimal: "18.4%". Null reads "—".
function levelPercent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

interface CardSpec {
  label: string;
  value: string;
  hint: string;
}

// The four-number instant read (blueprint §3.2: niveau / pente / courbure résument le smile, plus
// the realized-vs-implied edge). Index-keyed and side-agnostic — the put/call asymmetry IS the skew
// card, so the cards never split by side. ATM/skew/convexity are read from the projected smile/delta
// grid at the reference tenor (3m, else nearest); RV−IV is the persisted `iv_vs_realized` signal
// (the BFF computed it — we never recompute it here). A metric with no data shows "—".
export function Scorecards({
  maturities,
  ivVsRealized,
}: {
  maturities: AnalyticsMaturity[];
  // The persisted iv_vs_realized signal for the index at the reference tenor, or null when the
  // signal layer hasn't recorded one for this close.
  ivVsRealized: Signal | null;
}) {
  const card = computeScorecards(maturities);
  const tenorNote = card
    ? card.isReferenceTenor
      ? `at ${card.tenorLabel}`
      : `at ${card.tenorLabel} (3m not captured)`
    : "no surface";

  const cards: CardSpec[] = [
    {
      label: "ATM level",
      value: levelPercent(card?.atm ?? null),
      hint: `at-the-money implied vol · ${tenorNote}`,
    },
    {
      label: "Skew 25Δ",
      value: volPoints(card?.skew ?? null),
      hint: `risk-reversal: IV(25Δ put) − IV(25Δ call) · ${tenorNote}`,
    },
    {
      label: "Convexity 25Δ",
      value: volPoints(card?.convexity ?? null),
      hint: `butterfly: IV(25Δp) + IV(25Δc) − 2·ATM · ${tenorNote}`,
    },
    {
      // RV−IV: positive means the market moved more than options priced (realized rich vs implied).
      // Read straight off the persisted signal; the unit string travels with it from the BFF.
      label: "RV − IV",
      value: ivVsRealized ? volPoints(ivVsRealized.value) : "—",
      hint: ivVsRealized
        ? `realized − implied · ${ivVsRealized.tenor_label} (signal)`
        : "realized − implied · signal not recorded",
    },
  ];

  return (
    <section className="scorecards" aria-label="Volatility scorecards">
      {cards.map((c) => (
        <article key={c.label} className="scorecard" aria-label={c.label}>
          <p className="scorecard__label">{c.label}</p>
          <p className="scorecard__value">{c.value}</p>
          <p className="scorecard__hint">{c.hint}</p>
        </article>
      ))}
    </section>
  );
}
