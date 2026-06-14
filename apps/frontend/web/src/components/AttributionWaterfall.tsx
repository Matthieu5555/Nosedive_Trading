// The P&L attribution waterfall (TARGET §2 #5 / §7 #2; ADR 0030 waterfall path). Given one
// ScenarioAttribution record's payload it renders the by-Greek decomposition as a Plotly
// waterfall — Δ → Γ → Vega → Θ (→ Rho → Vanna → Volga once the seam carries them) → residual —
// each bar dollar-labelled with its unit string. The residual is its OWN bar (the honesty meter,
// §5.2), never hidden or folded into a term. The panel re-decomposes nothing: it plots the
// engine's dollar terms verbatim, the same numbers the BFF serialized.
//
// An honest empty/degraded state: when no attribution exists for the (book/portfolio, date) the
// payload is `found=false` and we render a labelled empty note, not a blank panel.

import type { Data, Layout } from "plotly.js";

import type { AttributionResponse } from "../api";
import { Plot } from "./Plot";
import { signedMoney } from "../lib/format";

// The residual reads as its own bar, distinct from the named terms, so the operator sees the
// honesty meter at a glance and never mistakes it for another Greek.
const RESIDUAL_NAME = "Residual";

export function AttributionWaterfall({
  attribution,
  kicker,
  emptyMessage = "No P&L attribution for this selection yet.",
}: {
  attribution: AttributionResponse;
  kicker: string;
  emptyMessage?: string;
}) {
  if (!attribution.found || attribution.terms.length === 0) {
    return (
      <article className="panel" aria-label="P&L attribution (empty)">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{kicker}</p>
            <h2>P&amp;L attribution</h2>
          </div>
        </div>
        <p role="status">{emptyMessage}</p>
      </article>
    );
  }

  const termUnit = attribution.terms[0]?.unit ?? "$";
  const residualUnit = attribution.residual.unit;

  // The waterfall: each named term is a relative step, the residual a final relative step, both
  // measured off the same zero. measure[] keeps every bar "relative" so the running total reads
  // as the cumulative explained PnL and the residual closes the gap to the full reprice.
  const names = [...attribution.terms.map((term) => term.name), RESIDUAL_NAME];
  const dollars = [
    ...attribution.terms.map((term) => term.dollars ?? 0),
    attribution.residual.dollars ?? 0,
  ];
  const textLabels = [
    ...attribution.terms.map((term) => `${signedMoney(term.dollars ?? 0)} (${term.unit})`),
    `${signedMoney(attribution.residual.dollars ?? 0)} (${residualUnit})`,
  ];

  const waterfall: Data = {
    type: "waterfall",
    orientation: "v",
    measure: names.map(() => "relative"),
    x: names,
    y: dollars,
    text: textLabels,
    textposition: "outside",
    name: "PnL attribution",
  } as unknown as Data;

  const label = `P&L attribution waterfall — ${kicker} (by-Greek dollar contributions + residual)`;
  const within = attribution.verdict?.within_tolerance;
  const layout: Partial<Layout> = {
    yaxis: { title: { text: `dollar PnL (${termUnit})` } },
    xaxis: { title: { text: "Greek contribution → residual" } },
  };

  return (
    <article className="panel attribution-panel" aria-label="P&L attribution">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">{kicker}</p>
          <h2>P&amp;L attribution</h2>
        </div>
        {within !== undefined && (
          <span className={within ? "status" : "status negative"}>
            {within ? "within tolerance" : "residual exceeds tolerance"}
          </span>
        )}
      </div>
      <p>
        Each bar is one Greek&apos;s dollar contribution to the scenario P&amp;L; the{" "}
        <strong>residual</strong> is the leftover against the full reprice (the honesty meter) —
        its own bar, never folded into a term. Bars in dollars:{" "}
        <strong>{termUnit}</strong>; residual: <strong>{residualUnit}</strong>.
      </p>
      <ul className="attribution-legend" aria-label="attribution terms">
        {attribution.terms.map((term) => (
          <li key={term.name}>
            {term.name}: <strong>{signedMoney(term.dollars ?? 0)}</strong> ({term.unit})
          </li>
        ))}
        <li>
          {RESIDUAL_NAME}: <strong>{signedMoney(attribution.residual.dollars ?? 0)}</strong> (
          {residualUnit})
        </li>
      </ul>
      <Plot label={label} data={[waterfall]} layout={layout} height={360} />
    </article>
  );
}
