import type { PlotProps } from "../components/Plot";

export function Plot({ data, label }: PlotProps) {
  const types = data.map((trace) => (trace as { type?: string }).type ?? "unknown").join(",");

  const z = (data[0] as { z?: unknown }).z;
  // Total plotted points across every trace's x (line/scatter panels carry no z) — lets a test
  // assert how many points survived cleaning without reaching into Plotly internals.
  const points = data.reduce(
    (total, trace) => total + ((trace as { x?: unknown[] }).x?.length ?? 0),
    0,
  );

  // The category labels along x and the per-point text labels, flattened across traces. Lets a test
  // assert which bars a categorical chart drew (e.g. the by-Greek waterfall) and what each is
  // labelled with, now that no separate legend re-prints those numbers beside the chart.
  const xLabels = data
    .map((trace) => ((trace as { x?: unknown[] }).x ?? []).join(" | "))
    .join(" | ");
  const textLabels = data
    .map((trace) => ((trace as { text?: unknown[] }).text ?? []).join(" | "))
    .join(" | ");

  return (
    <figure aria-label={label}>
      <figcaption>{label}</figcaption>
      <div data-testid="plot-types">{types}</div>
      <div data-testid="plot-z">{z === undefined ? "" : JSON.stringify(z)}</div>
      <div data-testid="plot-points">{points}</div>
      <div data-testid="plot-x">{xLabels}</div>
      <div data-testid="plot-text">{textLabels}</div>
    </figure>
  );
}
