// A labelled metric tile (Antho's demo) — an uppercase label over a tabular-nums value.
// Used in the quote strip, the Greek grid, the order preview, the scenario summary.

export function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
