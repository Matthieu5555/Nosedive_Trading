import type { Data, Layout } from "plotly.js";

import type { AttributionResponse } from "../api";
import { sci } from "../lib/format";
import { Plot } from "./Plot";

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

  const names = [...attribution.terms.map((term) => term.name), RESIDUAL_NAME];
  const dollars = [
    ...attribution.terms.map((term) => term.dollars ?? 0),
    attribution.residual.dollars ?? 0,
  ];
  const textLabels = [
    ...attribution.terms.map((term) => `${sci(term.dollars ?? 0)} (${term.unit})`),
    `${sci(attribution.residual.dollars ?? 0)} (${residualUnit})`,
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
        <strong>residual</strong> is the leftover against the full reprice (the honesty meter) — its
        own bar, never folded into a term. Bars in dollars: <strong>{termUnit}</strong>; residual:{" "}
        <strong>{residualUnit}</strong>.
      </p>
      <ul className="attribution-legend" aria-label="attribution terms">
        {attribution.terms.map((term) => (
          <li key={term.name}>
            {term.name}: <strong>{sci(term.dollars ?? 0)}</strong> ({term.unit})
          </li>
        ))}
        <li>
          {RESIDUAL_NAME}: <strong>{sci(attribution.residual.dollars ?? 0)}</strong> ({residualUnit}
          )
        </li>
      </ul>
      <Plot label={label} data={[waterfall]} layout={layout} height={360} />
    </article>
  );
}
