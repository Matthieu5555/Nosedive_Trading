// One accordion item per maturity (shadcn/Radix), each holding that maturity's 2D vol smile.
// The accordion lets the operator drill into one tenor's smile at a time. The dollar Greeks now
// live in the per-maturity TRANSPOSE table above (DollarGreeksByMaturity) — the single Greeks
// readout — so the accordion no longer carries a per-band matrix.

import * as Accordion from "@radix-ui/react-accordion";

import type { AnalyticsMaturity } from "../api";
import { SmileChart } from "./charts";

export function MaturityAccordion({
  maturities,
}: {
  maturities: AnalyticsMaturity[];
  // currency was used by the dropped per-band matrix; kept off the signature now.
}) {
  const label = "Per-maturity smile";
  if (maturities.length === 0) {
    return (
      <section aria-label={label}>
        <h3>{label}</h3>
        <p>No projected analytics for this ticker/date yet.</p>
      </section>
    );
  }
  return (
    <section aria-label={label}>
      <h3>{label}</h3>
      <Accordion.Root type="multiple" defaultValue={[maturities[0].label]}>
        {maturities.map((maturity) => (
          <Accordion.Item key={maturity.label} value={maturity.label}>
            <Accordion.Header>
              <Accordion.Trigger>{maturity.label}</Accordion.Trigger>
            </Accordion.Header>
            <Accordion.Content>
              <SmileChart maturity={maturity} />
            </Accordion.Content>
          </Accordion.Item>
        ))}
      </Accordion.Root>
    </section>
  );
}
