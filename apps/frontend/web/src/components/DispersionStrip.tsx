import { type Signal, SIGNAL_CAPTIONS } from "../api";
import { Stack } from "./layout";

// Average implied correlation ρ̄ as a percent, two decimals: "50.00%". ρ̄ lives in [-1, 1]; we show
// the magnitude an operator reads at a glance. Null reads "—" (the honest gap).
function correlationPercent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(2)}%`;
}

// The ρ̄ / dispersion strip — the secondary diagnostic at the BOTTOM of the page (ADR 0051: ρ̄ is the
// REALIZED-vol implied correlation read straight off the persisted `implied_correlation` signal, NOT
// a per-member implied-vol fan-out). It never leads; it's a single read, not the headline.
export function DispersionStrip({ index, signal }: { index: string; signal: Signal | null }) {
  if (signal === null) {
    return (
      <p className="dispersion-strip" role="status">
        No implied-correlation signal recorded for {index} on this close yet.
      </p>
    );
  }
  return (
    <Stack className="dispersion-strip" gap="2xs">
      <p className="dispersion-strip__value" aria-label="Implied correlation">
        ρ̄ = {correlationPercent(signal.value)}
        <span className="dispersion-strip__tenor"> · {signal.tenor_label}</span>
      </p>
      <p className="panel-note">{SIGNAL_CAPTIONS.implied_correlation}</p>
    </Stack>
  );
}
