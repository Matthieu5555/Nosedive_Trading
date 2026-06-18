import type { Data, Layout } from "plotly.js";

import type { AttributionResponse, RealizedAttributionResponse } from "../api";
import { sci } from "../lib/format";
import { Stack } from "./layout";
import { Plot } from "./Plot";

const RESIDUAL_NAME = "Residual";

export function AttributionWaterfall({
  attribution,
  kicker,
  emptyMessage = "No P&L attribution for this selection yet.",
  embedded = false,
}: {
  attribution: AttributionResponse;
  kicker: string;
  emptyMessage?: string;
  // When this component is dropped inside an already-titled card (e.g. RiskScenarios' "Where the
  // P&L came from"), its own <h2> + kicker would double the title. `embedded` suppresses that inner
  // heading. Default false keeps the standalone heading for callers that wrap it in a bare div.
  embedded?: boolean;
}) {
  if (!attribution.found || attribution.terms.length === 0) {
    return (
      <article className="panel" aria-label="P&L attribution (empty)">
        {!embedded && (
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">{kicker}</p>
              <h2>P&amp;L attribution</h2>
            </div>
          </div>
        )}
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

  const label = `P&L attribution waterfall, ${kicker} (by-Greek dollar contributions + residual)`;
  const within = attribution.verdict?.within_tolerance;
  const layout: Partial<Layout> = {
    yaxis: { title: { text: `dollar PnL (${termUnit})` } },
    xaxis: { title: { text: "Greek contribution → residual" } },
  };

  return (
    <article className="panel attribution-panel" aria-label="P&L attribution">
      <div className="panel-heading">
        {!embedded && (
          <div>
            <p className="panel-kicker">{kicker}</p>
            <h2>P&amp;L attribution</h2>
          </div>
        )}
        {within !== undefined && (
          <span className={within ? "status" : "status negative"}>
            {within ? "within tolerance" : "residual exceeds tolerance"}
          </span>
        )}
      </div>
      <p>
        Each bar is one Greek&apos;s dollar contribution to the scenario P&amp;L; the{" "}
        <strong>residual</strong> is the leftover against the full reprice (the honesty meter), its
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

// One plain-language sentence describing the market move that drove a single day, so a reader sees
// WHY the Greeks contributed what they did without reading the raw deltas. Only mentions a driver
// that actually moved (a flat rate day does not say "rates moved 0").
function moveSummary(move: RealizedAttributionStep["move"]): string {
  const parts: string[] = [];
  if (move.d_spot !== 0) {
    parts.push(`the underlying ${move.d_spot > 0 ? "rose" : "fell"} ${sci(Math.abs(move.d_spot))}`);
  }
  if (move.d_vol !== 0) {
    parts.push(
      `implied vol ${move.d_vol > 0 ? "rose" : "fell"} ${sci(Math.abs(move.d_vol))} vol points`,
    );
  }
  if (move.d_rate !== 0) {
    parts.push(`rates ${move.d_rate > 0 ? "rose" : "fell"} ${sci(Math.abs(move.d_rate))}`);
  }
  if (parts.length === 0) return "Only the passage of time moved the position this day.";
  return `Over this day, ${parts.join(", ")}.`;
}

type RealizedAttributionStep = RealizedAttributionResponse["steps"][number];

// One day's waterfall: the seven by-Greek contributions, the residual as its own honesty bar, plus
// the approximate-vs-full-reprice line so the reader sees the approximation error in dollars.
function RealizedStepCard({
  step,
  index,
}: {
  step: RealizedAttributionStep;
  index: number;
}) {
  const termUnit = step.terms[0]?.unit ?? step.approx_pnl.unit ?? "$";
  const residualUnit = step.residual.unit;

  const names = [...step.terms.map((t) => t.name), RESIDUAL_NAME];
  const dollars = [...step.terms.map((t) => t.dollars ?? 0), step.residual.dollars ?? 0];
  const textLabels = [
    ...step.terms.map((t) => sci(t.dollars ?? 0)),
    sci(step.residual.dollars ?? 0),
  ];

  const waterfall: Data = {
    type: "waterfall",
    orientation: "v",
    measure: names.map(() => "relative"),
    x: names,
    y: dollars,
    text: textLabels,
    textposition: "outside",
    name: "realized day attribution",
  } as unknown as Data;

  const within = step.verdict.within_tolerance;
  const label = `Realized P&L attribution, ${step.start_date} to ${step.end_date} (by-Greek dollar contributions + residual)`;
  const layout: Partial<Layout> = {
    yaxis: { title: { text: `dollar PnL (${termUnit})` } },
    xaxis: { title: { text: "Greek contribution → residual" } },
  };

  return (
    <article className="panel attribution-panel" aria-label={`Realized attribution day ${index + 1}`}>
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">
            {step.start_date} → {step.end_date}
          </p>
          <h3>
            Day {index + 1}: {sci(step.full_reprice_pnl.dollars ?? 0)} {termUnit} of realized P&amp;L
          </h3>
        </div>
        <span className={within ? "status" : "status negative"}>
          {within ? "within tolerance" : "residual exceeds tolerance"}
        </span>
      </div>
      <p>{moveSummary(step.move)}</p>
      <ul className="attribution-legend" aria-label={`day ${index + 1} attribution terms`}>
        {step.terms.map((term) => (
          <li key={term.name}>
            {term.name}: <strong>{sci(term.dollars ?? 0)}</strong> ({term.unit})
          </li>
        ))}
        <li>
          {RESIDUAL_NAME}: <strong>{sci(step.residual.dollars ?? 0)}</strong> ({residualUnit})
        </li>
      </ul>
      <p>
        These seven Greek contributions sum to an approximate{" "}
        <strong>{sci(step.approx_pnl.dollars ?? 0)}</strong>; the day actually re-priced to{" "}
        <strong>{sci(step.full_reprice_pnl.dollars ?? 0)}</strong>, so the leftover the Greeks did
        not explain (the <strong>residual</strong>) is{" "}
        <strong>{sci(step.residual.dollars ?? 0)}</strong> {residualUnit}.
      </p>
      <Plot label={label} data={[waterfall]} layout={layout} height={320} />
    </article>
  );
}

// The realized day-over-day view: one waterfall per day the position was held, each showing the
// seven by-Greek contributions and the honest residual against the full reprice. Reuses the same
// per-Greek waterfall the scenario view draws, one card per day.
export function RealizedAttributionWaterfall({
  realized,
  emptyMessage = "No realized attribution for this position yet.",
}: {
  realized: RealizedAttributionResponse;
  emptyMessage?: string;
}) {
  if (!realized.found || realized.steps.length === 0) {
    return (
      <article className="panel" aria-label="Realized attribution (empty)">
        <p role="status">{emptyMessage}</p>
      </article>
    );
  }

  return (
    <Stack gap="md">
      <article className="panel" aria-label="Realized attribution overview">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">
              {realized.underlying} {realized.expiry}
            </p>
            <h2>What drove the position's P&amp;L, day by day</h2>
          </div>
        </div>
        <p>
          For each day the position was held, we read the day's actual change in value back as the
          contribution of each Greek (Delta, Gamma, Vega, Theta, Rho, Vanna, Volga). The bars add up
          to an approximation; the <strong>residual</strong> bar is the small leftover the Greeks do
          not capture, shown honestly against a full re-pricing so you can see the approximation
          error.
        </p>
        <ul className="attribution-legend" aria-label="realized attribution position">
          <li>
            Position:{" "}
            <strong>
              {realized.contracts.length} contract{realized.contracts.length === 1 ? "" : "s"}
            </strong>{" "}
            ({realized.contracts.join(", ")})
          </li>
          <li>
            Days covered: <strong>{realized.dates.join(", ")}</strong>
          </li>
        </ul>
      </article>
      {realized.steps.map((step, index) => (
        <RealizedStepCard key={`${step.start_date}-${step.end_date}`} step={step} index={index} />
      ))}
    </Stack>
  );
}
