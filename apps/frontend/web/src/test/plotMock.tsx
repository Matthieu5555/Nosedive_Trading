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
  return (
    <figure aria-label={label}>
      <figcaption>{label}</figcaption>
      <div data-testid="plot-types">{types}</div>
      <div data-testid="plot-z">{z === undefined ? "" : JSON.stringify(z)}</div>
      <div data-testid="plot-points">{points}</div>
    </figure>
  );
}
