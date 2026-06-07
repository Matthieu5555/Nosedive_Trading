// One-click multi-leg templates for the basket builder (WS 2A).
//
// The course's three pillars are −30Δ put, ATM, +30Δ call (the analytics grid samples that span;
// the simple scenario uses only the ±30 wings + ATM). The standard realizations:
//
//   - strangle      : long the +30Δ call + long the −30Δ put (the two OTM wing cells).
//   - risk_reversal : long the +30Δ call, short the −30Δ put (the skew trade).
//   - straddle      : long the ATM call (`atm`) + long the ATM put (`atmp`) — a call AND a put at
//                     the SAME ATM-forward strike, ~delta-neutral and long gamma/vega. The grid
//                     now emits both ATM pillars (WS 1F-followup, `tasks/1F-atm-put-cell.md`), so
//                     this is the genuine two-leg straddle, not the ±30Δ wings (that pair is the
//                     strangle above — a straddle and a strangle must not compose identical legs).

import type { BasketLegInput } from "./api";

export type TemplateName = "straddle" | "strangle" | "risk_reversal";

export const TEMPLATE_LABELS: Record<TemplateName, string> = {
  straddle: "Straddle (long ATM call + ATM put)",
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
      // The two ATM legs at the one ATM-forward strike: the call (`atm`) and the put (`atmp`).
      // NOT the ±30Δ pair (that is the strangle).
      return [
        optionLeg("long", underlying, tenorLabel, "atm"),
        optionLeg("long", underlying, tenorLabel, "atmp"),
      ];
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
