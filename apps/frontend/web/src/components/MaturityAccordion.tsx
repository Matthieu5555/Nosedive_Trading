// One accordion item per maturity (shadcn/Radix), each holding the 2D smile and that
// maturity's dollar Greeks with their unit strings. The accordion lets the operator drill into
// one tenor at a time without scrolling a wall of grids.

import * as Accordion from "@radix-ui/react-accordion";

import type { AnalyticsMaturity } from "../api";
import { DollarGreeksMatrix } from "./DollarGreeks";
import { SmileChart } from "./charts";

export function MaturityAccordion({ maturities }: { maturities: AnalyticsMaturity[] }) {
  const label = "Per-maturity smile and dollar Greeks";
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
              <DollarGreeksMatrix points={maturity.points} />
            </Accordion.Content>
          </Accordion.Item>
        ))}
      </Accordion.Root>
    </section>
  );
}
