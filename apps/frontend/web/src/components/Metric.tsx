import { sciUnit } from "../lib/format";
import { InfoDot } from "./InfoDot";

interface MetricProps {
  label: string;
  value: string | number | null | undefined;
  unit?: string | null;
  hint?: string;
}

function render(
  value: string | number | null | undefined,
  unit: string | null | undefined,
): string {
  if (typeof value === "number") return sciUnit(value, unit);
  if (value === null || value === undefined) return "—";
  if (unit) return `${value} ${unit}`;
  return value;
}

export function Metric({ label, value, unit, hint }: MetricProps) {
  return (
    <div className="metric">
      <span>
        {label}
        {hint ? <InfoDot label={`${label} — provenance`} body={hint} /> : null}
      </span>
      <strong>{render(value, unit)}</strong>
    </div>
  );
}
