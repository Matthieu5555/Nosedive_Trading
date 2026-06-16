import type { PlotProps } from "../components/Plot";

export function Plot({ data, label }: PlotProps) {
  const types = data.map((trace) => (trace as { type?: string }).type ?? "unknown").join(",");

  const z = (data[0] as { z?: unknown }).z;
  return (
    <figure aria-label={label}>
      <figcaption>{label}</figcaption>
      <div data-testid="plot-types">{types}</div>
      <div data-testid="plot-z">{z === undefined ? "" : JSON.stringify(z)}</div>
    </figure>
  );
}
