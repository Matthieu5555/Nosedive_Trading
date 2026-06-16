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
