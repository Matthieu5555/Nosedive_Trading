// One-click multi-leg templates for the basket builder (WS 2A).
//
// The course's three pillars are −30Δ put, ATM, +30Δ call (the analytics grid samples that span;
// the simple scenario uses only the ±30 wings + ATM). The standard realizations:
//
//   - strangle      : long the +30Δ call + long the −30Δ put (the two OTM wing cells).
//   - risk_reversal : long the +30Δ call, short the −30Δ put (the skew trade).
//   - straddle      : a call AND a put at the SAME ATM strike — its defining property is being
//                     ~delta-neutral and max gamma/vega (an ATM option is ~50Δ; the ±30Δ wings are
//                     NOT a straddle — that pair is exactly the strangle above).
//
// KNOWN LIMITATION (interim). A true ATM straddle needs an ATM call AND an ATM put at the ATM
// strike. The grid stores a single ATM cell, priced as a CALL (target-delta-0 → right "C"); there
// is no ATM-put cell. So we cannot compose a genuine two-leg ATM straddle by summing grid cells
// today. The correct fix is upstream (WS 1F): emit an explicit ATM-put cell so the straddle is two
// real legs (long ATM call + long ATM put). Until that lands, the straddle button is the honest
// degenerate: long the single ATM cell (~50Δ, the right strike — the max-gamma/vega point — but
// only half the straddle). We deliberately do NOT alias it to the ±30Δ pair, which would make the
// straddle and strangle buttons compose identical legs. (Expert review, 2026-06-07; see
// tasks/2A-basket-builder.md.)

import type { BasketLegInput } from "./api";

export type TemplateName = "straddle" | "strangle" | "risk_reversal";

export const TEMPLATE_LABELS: Record<TemplateName, string> = {
  straddle: "Straddle (ATM, ½ — needs an ATM-put cell for the full two legs)",
  strangle: "Strangle (long ±30Δ wings)",
  risk_reversal: "Risk reversal (long +30Δ call / short −30Δ put)",
};

function optionLeg(
  side: "long" | "short",
  underlying: string,
  tenorLabel: string,
  deltaBand: string,
): BasketLegInput {
  return {
    instrument_kind: "option",
    side,
    quantity: side === "long" ? 1 : -1,
    underlying,
    tenor_label: tenorLabel,
    delta_band: deltaBand,
  };
}

export function buildTemplate(
  name: TemplateName,
  underlying: string,
  tenorLabel: string,
): BasketLegInput[] {
  switch (name) {
    case "straddle":
      // Interim: the single ATM leg (~50Δ). Becomes [long atm call + long atm put] once WS 1F
      // emits an ATM-put cell. NOT the ±30Δ pair (that is the strangle).
      return [optionLeg("long", underlying, tenorLabel, "atm")];
    case "strangle":
      return [
        optionLeg("long", underlying, tenorLabel, "30dc"),
        optionLeg("long", underlying, tenorLabel, "30dp"),
      ];
    case "risk_reversal":
      return [
        optionLeg("long", underlying, tenorLabel, "30dc"),
        optionLeg("short", underlying, tenorLabel, "30dp"),
      ];
  }
}
